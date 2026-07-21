"""HTTP-path tests for the client (sandbox=False). Sandbox routing is covered in
phase 4 once csiapps.sandbox exists.

Ports the production-path assertions of test-sandbox-mode.R (token required when
sandbox is off) and adds coverage for the fetch_* helpers and pagination.
"""

import asyncio

import httpx
import pytest
import respx
from shiny import reactive

from csiapps import client, config
from csiapps.client import (
    current_token,
    fetch_org_options,
    fetch_profile,
    fetch_profiles,
    flatten_profile,
    make_request,
    token_ready,
)

SITE = "https://apps.csipacific.ca"


# ---- token resolution ----


def test_current_token_env_fallback(monkeypatch):
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    assert current_token() == "envtok"


def test_current_token_session_wins(monkeypatch):
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")

    class FakeSession:  # weak-referenceable stand-in for a Shiny session
        pass

    sess = FakeSession()
    monkeypatch.setattr(client, "_get_current_session", lambda: sess)
    client.set_session_token(sess, "sesstok")
    assert current_token() == "sesstok"
    client.set_session_token(sess, None)
    assert current_token() == "envtok"


def test_token_ready_env_fallback(monkeypatch):
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    assert token_ready() is False
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    assert token_ready() is True


def test_production_requires_token(monkeypatch):
    # sandbox off + no token, outside any reactive context -> raise loudly (the R
    # "production path still requires a token" case, and the CLI/script path).
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="no CSIAPPS_ACCESS_TOKEN set"):
        make_request("api/csiauth/me/", sandbox=False)


def test_gates_quietly_and_refires_on_login(monkeypatch):
    # Inside a reactive context a not-yet-available token must not raise: the
    # helper cancels quietly via req(), and the reactive re-runs on its own once
    # server_wrapper stores the token (the fix for the empty-dropdown race).
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)

    class FakeSession:  # weak-referenceable stand-in for a Shiny session
        pass

    sess = FakeSession()
    monkeypatch.setattr(client, "_get_current_session", lambda: sess)
    client.set_session_token(sess, None)

    state = {"runs": 0, "reached_api": 0}

    @reactive.effect
    def _consumer():
        state["runs"] += 1
        # A gated helper called before login: raises no error, cancels quietly.
        fetch_org_options(sandbox=False)
        state["reached_api"] += 1

    async def drive():
        await reactive.flush()
        assert state["runs"] == 1
        assert state["reached_api"] == 0  # gated pre-login, no error surfaced

        client.set_session_token(sess, "tok-abc")
        await reactive.flush()
        assert state["runs"] == 2  # re-fired on its own once the token arrived
        assert state["reached_api"] == 1  # got past the gate and completed the call

    with respx.mock:
        respx.get(f"{SITE}{config.SPORT_ORG_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        asyncio.run(drive())

    _consumer.destroy()


# ---- make_request HTTP path ----


@respx.mock
def test_make_request_get_parses_json():
    respx.get(f"{SITE}/api/csiauth/me/").mock(
        return_value=httpx.Response(200, json={"first_name": "Ada"})
    )
    out = make_request("api/csiauth/me/", token="tok", sandbox=False)
    assert out == {"first_name": "Ada"}


@respx.mock
def test_make_request_http_error_raises():
    respx.get(f"{SITE}/api/thing").mock(return_value=httpx.Response(403, text="nope"))
    with pytest.raises(RuntimeError, match=r"API request failed \(403\)"):
        make_request("api/thing", token="tok", sandbox=False)


@respx.mock
def test_make_request_empty_body_is_empty_list():
    respx.get(f"{SITE}/api/empty").mock(return_value=httpx.Response(200, text=""))
    assert make_request("api/empty", token="tok", sandbox=False) == []


@respx.mock
def test_make_request_paginates():
    url = f"{SITE}/api/warehouse/data-records"
    respx.get(url).side_effect = [
        httpx.Response(200, json={"results": [1], "next": f"{url}?page=2"}),
        httpx.Response(200, json={"results": [2], "next": None}),
    ]
    pages = make_request(
        "api/warehouse/data-records", token="tok", sandbox=False, paginate=True
    )
    assert [p["results"] for p in pages] == [[1], [2]]


# ---- fetch_org_options ----


@respx.mock
def test_fetch_org_options_maps_results():
    respx.get(f"{SITE}{config.SPORT_ORG_ENDPOINT}").mock(
        return_value=httpx.Response(
            200, json={"results": [{"name": "Rowing Canada", "id": 42}]}
        )
    )
    # {value: label} -- plugs straight into ui.input_select(choices=...)
    assert fetch_org_options(token="tok", sandbox=False) == {42: "Rowing Canada"}


def test_fetch_org_options_requires_token():
    with pytest.raises(RuntimeError, match="no CSIAPPS_ACCESS_TOKEN set"):
        fetch_org_options(sandbox=False)


# ---- fetch_profiles ----


@respx.mock
def test_fetch_profiles_accumulates_pages():
    url = f"{SITE}{config.PROFILE_ENDPOINT}"
    route = respx.get(url)
    route.side_effect = [
        httpx.Response(200, json={"results": [{"id": 1}], "next": f"{url}?offset=100"}),
        httpx.Response(200, json={"results": [{"id": 2}], "next": None}),
    ]
    profiles = fetch_profiles(token="tok", sandbox=False)
    assert [p["id"] for p in profiles] == [1, 2]


@respx.mock
def test_fetch_profiles_bounds_runaway_pagination():
    # A misbehaving server whose `next` never terminates must not hang the app in
    # an unbounded loop; pagination is capped by max_pages.
    url = f"{SITE}{config.PROFILE_ENDPOINT}"
    call_count = {"n": 0}

    def _never_ending(request):
        call_count["n"] += 1
        n = call_count["n"]
        return httpx.Response(
            200, json={"results": [{"id": n}], "next": f"{url}?page={n + 1}"}
        )

    respx.get(url__startswith=url).mock(side_effect=_never_ending)
    with pytest.warns(UserWarning, match="truncated"):
        profiles = fetch_profiles(token="tok", sandbox=False, max_pages=5)
    assert len(profiles) == 5
    assert call_count["n"] == 5


@respx.mock
def test_fetch_profiles_stops_on_cycled_next():
    # If the server cycles `next` back to a URL already fetched, stop rather than
    # loop forever.
    url = f"{SITE}{config.PROFILE_ENDPOINT}"
    page2 = f"{url}?page=2"

    def _cycle(request):
        if str(request.url) == page2:
            return httpx.Response(200, json={"results": [{"id": 2}], "next": str(request.url)})
        return httpx.Response(200, json={"results": [{"id": 1}], "next": page2})

    respx.get(url__startswith=url).mock(side_effect=_cycle)
    profiles = fetch_profiles(token="tok", sandbox=False, max_pages=50)
    # page1 -> id 1 (next=page2), page2 -> id 2 (next=page2, already seen -> stop)
    assert [p["id"] for p in profiles] == [1, 2]


# ---- fetch_profile ----


@respx.mock
def test_fetch_profile_returns_json_and_encodes_id():
    respx.get(f"{SITE}{config.PROFILE_ENDPOINT}12%2F3").mock(
        return_value=httpx.Response(200, json={"id": "12/3"})
    )
    out = fetch_profile("12/3", token="tok", sandbox=False)
    assert out == {"id": "12/3"}


# ---- flatten helpers ----


def test_flatten_profile_is_scalar_row():
    p = {
        "id": 1,
        "person": {"first_name": "Ada", "last_name": "L", "email": "a@x.com", "dob": "2000-01-01"},
        "sport": {"id": 100, "name": "Rowing"},
        "status": "ACTIVE",
    }
    row = flatten_profile(p)
    assert row == {
        "id": 1,
        "first_name": "Ada",
        "last_name": "L",
        "email": "a@x.com",
        "dob": "2000-01-01",
        "sport_id": 100,
        "sport": "Rowing",
        "status": "ACTIVE",
    }
    assert all(not isinstance(v, (dict, list)) for v in row.values())
