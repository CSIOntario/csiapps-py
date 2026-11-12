"""R 0.2.1 request parity, using HTTP mocks and no framework dependencies."""
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from csiapps import AuthenticationError, auth, client, config

SITE = "https://apps.csipacific.ca"
URL = f"{SITE}/api/records/"


class SessionAdapter:
    def __init__(self, token):
        self.token = token

    def read_token(self):
        return self.token

    def invalidate_token(self, token):
        if self.token != token:
            return False
        self.token = ""
        return True

    def handle_missing_token(self):
        return False


@pytest.fixture
def session_adapter(monkeypatch):
    adapter = SessionAdapter("session-token")
    monkeypatch.setattr(client, "_token_adapters", [adapter])
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "console-token")
    return adapter


@pytest.mark.parametrize("status", [401, 403, 500])
@pytest.mark.parametrize("body", [b"", b'{"detail":"rejected"}'])
@respx.mock
def test_error_status_precedes_empty_body(session_adapter, status, body):
    route = respx.get(URL).respond(status, content=body)
    with pytest.raises(RuntimeError, match=str(status)) as error:
        client.make_request("api/records/", sandbox=False, retry_max_tries=1)
    assert route.call_count == 1
    assert session_adapter.token == ("" if status == 401 else "session-token")
    if status == 401:
        assert isinstance(error.value, AuthenticationError)
        assert error.value.session_expired
        assert not client.token_ready()


@pytest.mark.parametrize("kind", ["explicit", "header", "replacement"])
@respx.mock
def test_unrelated_or_old_rejection_does_not_clear_session(session_adapter, kind):
    def reject(request):
        if kind == "replacement":
            session_adapter.token = "new-token"
        return httpx.Response(401)
    respx.get(URL).mock(side_effect=reject)
    kwargs = ({"token": "other-token"} if kind == "explicit" else
              {"headers": {"authorization": "Bearer other-token"}} if kind == "header" else {})
    with pytest.raises(AuthenticationError) as error:
        client.make_request("api/records/", sandbox=False, **kwargs)
    assert not error.value.session_expired
    assert session_adapter.token == ("new-token" if kind == "replacement" else "session-token")


@pytest.mark.parametrize("call", [
    lambda: client.fetch_profiles(sandbox=False),
    lambda: client.fetch_profile(1, sandbox=False),
    lambda: client.fetch_org_options(sandbox=False),
])
@respx.mock
def test_registration_helpers_invalidate_session(session_adapter, call):
    respx.route().respond(401)
    with pytest.raises(AuthenticationError):
        call()
    assert not client.token_ready()


@pytest.mark.parametrize("next_field", [{}, {"next": None}])
@pytest.mark.parametrize("start", [0, 2])
@respx.mock
def test_count_pagination_preserves_repeated_subjects_and_server_caps(next_field, start):
    urls = []
    def page(request):
        urls.append(request.url)
        offset = int(request.url.params.get("offset", 0))
        assert request.url.params.get_list("subject") == ["1", "2"]
        assert request.url.params["limit"] == "500"
        return httpx.Response(200, json={
            "count": 6, "results": list(range(offset, offset + 2)), **next_field,
        })
    respx.get(URL).mock(side_effect=page)
    pages = client.make_request("api/records/", token="fake", sandbox=False, paginate=True,
                                query={"subject": [1, 2], "limit": 500, "offset": start})
    assert [row for page in pages for row in page["results"]] == list(range(start, 6))
    assert len(urls) == (6 - start) // 2


@pytest.mark.parametrize("complete", [False, True])
@respx.mock
def test_page_bound_allows_exact_completion_only(complete):
    respx.get(URL).respond(200, json={"count": 1 if complete else 2, "results": [1]})
    kwargs = dict(token="fake", sandbox=False, paginate=True, max_pages=1)
    if complete:
        assert len(client.make_request("api/records/", **kwargs)) == 1
    else:
        with pytest.raises(RuntimeError, match="max_pages"):
            client.make_request("api/records/", **kwargs)


@pytest.mark.parametrize("body, message", [
    ({"count": 2, "results": [], "next": None}, "before all records"),
    ({"next": URL, "results": [1]}, "repeated"),
    ({"next": "https://another.example/records", "results": [1]}, "another origin"),
    ([], "JSON object"),
])
@respx.mock
def test_incomplete_pagination_fails(body, message):
    respx.get(URL).respond(200, json=body)
    with pytest.raises(RuntimeError, match=message):
        client.make_request("api/records/", token="fake", sandbox=False, paginate=True)


@respx.mock
def test_relative_next_then_count_fallback():
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(200, json={"count": 3, "results": [1], "next": "?offset=1&subject=7"}),
        httpx.Response(200, json={"count": 3, "results": [2], "next": None}),
        httpx.Response(200, json={"count": 3, "results": [3]}),
    ]
    pages = client.make_request("api/records/", token="fake", sandbox=False, paginate=True)
    assert len(pages) == 3
    assert route.calls[2].request.url.params == httpx.QueryParams({"offset": 2, "subject": 7})


@pytest.mark.parametrize("option", ["max_pages", "retry_max_tries"])
@pytest.mark.parametrize("value", [0, -1, 1.5, True, "2"])
def test_invalid_bounds_fail_before_network(option, value):
    with pytest.raises(ValueError, match=option):
        client.make_request("api/records/", token="fake", sandbox=False, paginate=True,
                            **{option: value})


@pytest.mark.parametrize("tries", [1, 3])
@respx.mock
def test_write_retry_control(monkeypatch, tries):
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    route = respx.post(URL).respond(503)
    with pytest.raises(RuntimeError, match="503"):
        client.make_request("api/records/", method="POST", body={"records": [1]},
                            token="fake", sandbox=False, retry_max_tries=tries)
    assert route.call_count == tries


@respx.mock
def test_custom_retry_and_empty_success(monkeypatch):
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    route = respx.get(URL)
    route.side_effect = [httpx.Response(409), httpx.Response(204)]
    assert client.make_request("api/records/", token="fake", sandbox=False,
                               retry_is_transient=lambda resp: resp.status_code == 409) == []
    assert route.call_count == 2


@respx.mock
def test_custom_retry_never_repeats_401(session_adapter):
    route = respx.get(URL).respond(401)
    with pytest.raises(AuthenticationError):
        client.make_request("api/records/", sandbox=False, retry_is_transient=lambda _: True)
    assert route.call_count == 1


@pytest.mark.parametrize("secret", ["", "client-secret"])
@respx.mock
def test_public_and_confidential_pkce_clients(monkeypatch, secret):
    monkeypatch.setenv("CSIAPPS_CLIENT_ID", "client-id")
    monkeypatch.setenv("CSIAPPS_CLIENT_SECRET", secret)
    monkeypatch.setenv("CSIAPPS_REDIRECT_URI", "http://localhost/callback")
    assert auth.check_secrets(sandbox=False)
    route = respx.post(config.token_url()).respond(200, json={"access_token": "fake"})
    assert auth.exchange_code_for_token("code", "verifier")["access_token"] == "fake"
    request = route.calls.last.request
    assert parse_qs(request.content.decode())["client_id"] == ["client-id"]
    assert parse_qs(request.content.decode())["code_verifier"] == ["verifier"]
    assert ("authorization" in request.headers) == bool(secret)
