"""Tests for native Streamlit PKCE authentication and page chrome."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("streamlit", reason="csiapps[streamlit] not installed")
pytest.importorskip("cryptography", reason="csiapps[streamlit] not installed")

from csiapps import client  # noqa: E402
from csiapps import streamlit as csist  # noqa: E402

GOOD_KEY = "k" * 48
REDIRECT = "https://example-app.share.connect.posit.cloud/"


class QueryParams(dict):
    def to_dict(self):
        return dict(self)

    def from_dict(self, values):
        self.clear()
        self.update(values)


class Rerun(Exception):
    pass


class Stop(Exception):
    pass


@pytest.fixture
def prod_env(monkeypatch):
    monkeypatch.setenv("CSIAPPS_CLIENT_ID", "test-client")
    monkeypatch.setenv("CSIAPPS_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("CSIAPPS_REDIRECT_URI", REDIRECT)
    monkeypatch.setenv("CSIAPPS_SECRET_KEY", GOOD_KEY)
    monkeypatch.setattr(csist, "_context_url", lambda: REDIRECT)


@pytest.fixture
def streamlit_state(monkeypatch):
    query = QueryParams()
    session = {}
    context = SimpleNamespace(cookies={}, url=REDIRECT)
    monkeypatch.setattr(csist.st, "query_params", query)
    monkeypatch.setattr(csist.st, "session_state", session)
    monkeypatch.setattr(csist.st, "context", context)
    monkeypatch.setattr(csist.st, "rerun", lambda: (_ for _ in ()).throw(Rerun()))
    return query, session, context


def sealed_state(*, now=1000, browser_nonce="browser"):
    return csist._encrypt_state(
        GOOD_KEY,
        verifier="pkce-verifier",
        browser_nonce=browser_nonce,
        redirect_uri=REDIRECT,
        return_query={"year": "2026"},
        now=now,
    )


def test_state_round_trip_is_encrypted_and_browser_bound():
    state = sealed_state()
    assert "pkce-verifier" not in state
    payload = csist._decrypt_state(
        state,
        GOOD_KEY,
        browser_nonce="browser",
        redirect_uri=REDIRECT,
        now=1001,
    )
    assert payload["v"] == "pkce-verifier"
    assert payload["q"] == {"year": "2026"}


@pytest.mark.parametrize(
    "change",
    [
        {"value": lambda state: state[:-1] + ("A" if state[-1] != "A" else "B")},
        {"secret_key": "x" * 48},
        {"browser_nonce": "another-browser"},
        {"redirect_uri": "https://other.example/"},
        {"now": 1000 + csist.AUTH_TTL_SECONDS + 1},
    ],
)
def test_state_rejects_tampering_wrong_binding_and_expiry(change):
    state = sealed_state()
    args = {
        "value": state,
        "secret_key": GOOD_KEY,
        "browser_nonce": "browser",
        "redirect_uri": REDIRECT,
        "now": 1001,
    }
    args.update(change)
    if callable(args["value"]):
        args["value"] = args["value"](state)
    with pytest.raises(ValueError, match="Invalid or expired"):
        csist._decrypt_state(**args)


def test_login_transaction_uses_pkce_and_preserves_non_auth_query(
    monkeypatch, prod_env, streamlit_state
):
    query, _, _ = streamlit_state
    query.update({"year": "2026", "code": "discard"})
    monkeypatch.setattr(
        csist.auth,
        "generate_pkce",
        lambda: {"verifier": "verifier", "challenge": "challenge", "method": "S256"},
    )
    url, browser_nonce = csist._login_transaction(GOOD_KEY, REDIRECT)
    params = parse_qs(urlparse(url).query)

    assert params["client_id"] == ["test-client"]
    assert params["redirect_uri"] == [REDIRECT]
    assert params["code_challenge"] == ["challenge"]
    assert params["code_challenge_method"] == ["S256"]
    transaction = csist._decrypt_state(
        params["state"][0],
        GOOD_KEY,
        browser_nonce=browser_nonce,
        redirect_uri=REDIRECT,
    )
    assert transaction["v"] == "verifier"
    assert transaction["q"] == {"year": "2026"}


def test_successful_callback_stores_token_server_side_and_restores_query(
    monkeypatch, prod_env, streamlit_state
):
    query, session, context = streamlit_state
    session[csist.LOGIN_PAUSED_KEY] = True
    context.cookies[csist.COOKIE_NAME] = "browser"
    query.update({"code": "auth-code", "state": sealed_state()})
    captured = {}

    def exchange(code, verifier):
        captured.update(code=code, verifier=verifier)
        return {"access_token": "browser-token", "expires_in": 3600}

    monkeypatch.setattr(csist.auth, "exchange_code_for_token", exchange)
    monkeypatch.setattr(
        csist, "_userinfo", lambda token: {"first_name": "Ada", "last_name": "Lovelace"}
    )
    monkeypatch.setattr(csist.time, "time", lambda: 1001)

    with pytest.raises(Rerun):
        csist._finish_login(GOOD_KEY, REDIRECT)

    assert captured == {"code": "auth-code", "verifier": "pkce-verifier"}
    assert session[csist.SESSION_KEY]["access_token"] == "browser-token"
    assert session[csist.SESSION_KEY]["expires_at"] == 4601
    assert csist.LOGIN_PAUSED_KEY not in session
    assert query == {"year": "2026"}


@pytest.mark.parametrize(
    ("query_values", "cookie", "message"),
    [
        ({"error": "access_denied", "state": "state"}, "browser", "denied"),
        ({"code": "code"}, "browser", "Invalid or expired"),
        ({"code": "code", "state": "state"}, "", "Invalid or expired"),
    ],
)
def test_callback_rejects_denied_or_incomplete_response(
    prod_env, streamlit_state, query_values, cookie, message
):
    query, session, context = streamlit_state
    query.update(query_values)
    if cookie:
        context.cookies[csist.COOKIE_NAME] = cookie
    assert message in csist._finish_login(GOOD_KEY, REDIRECT)
    assert not session
    assert not query


def test_failed_exchange_never_creates_session(
    monkeypatch, prod_env, streamlit_state
):
    query, session, context = streamlit_state
    context.cookies[csist.COOKIE_NAME] = "browser"
    query.update({"code": "auth-code", "state": sealed_state()})
    monkeypatch.setattr(
        csist.auth, "exchange_code_for_token", lambda code, verifier: {"error": "bad"}
    )
    monkeypatch.setattr(csist.time, "time", lambda: 1001)
    assert "token exchange failed" in csist._finish_login(GOOD_KEY, REDIRECT)
    assert not session
    assert not query


def test_tampered_callback_state_is_reported_as_invalid(prod_env, streamlit_state):
    query, session, context = streamlit_state
    context.cookies[csist.COOKIE_NAME] = "browser"
    query.update({"code": "auth-code", "state": "not-valid-base64!"})
    assert "Invalid or expired" in csist._finish_login(GOOD_KEY, REDIRECT)
    assert not session
    assert not query


def test_expired_session_is_removed(monkeypatch, streamlit_state):
    _, session, _ = streamlit_state
    session[csist.SESSION_KEY] = {"access_token": "old", "expires_at": 10}
    monkeypatch.setattr(csist.time, "time", lambda: 11)
    assert csist._streamlit_session() is None
    assert csist.SESSION_KEY not in session


def test_streamlit_adapter_reads_only_session_token(streamlit_state):
    _, session, _ = streamlit_state
    adapter = csist._StreamlitTokenAdapter()
    assert adapter.read_token() is None
    session[csist.SESSION_KEY] = {"access_token": "per-user"}
    assert adapter.read_token() == "per-user"
    assert client.current_token() == "per-user"


def test_production_config_accepts_connect_cloud_root(prod_env):
    secret, redirect, secure, path = csist._require_production_config()
    assert secret == GOOD_KEY
    assert redirect == REDIRECT
    assert secure is True
    assert path == "/"


@pytest.mark.parametrize(
    ("redirect", "current", "message"),
    [
        ("http://public.example/app", None, "must use HTTPS"),
        ("https:///missing-host", None, "absolute public app URL"),
        ("https://example.com/app?query=1", None, "absolute public app URL"),
        ("https://example.com/app", "https://example.com/other", "exactly match"),
    ],
)
def test_production_config_rejects_unsafe_or_mismatched_redirect(
    monkeypatch, prod_env, redirect, current, message
):
    monkeypatch.setenv("CSIAPPS_REDIRECT_URI", redirect)
    monkeypatch.setattr(csist, "_context_url", lambda: current)
    with pytest.raises(ValueError, match=message):
        csist._require_production_config()


def test_production_config_requires_long_stable_secret(monkeypatch, prod_env):
    monkeypatch.setenv("CSIAPPS_SECRET_KEY", "short")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        csist._require_production_config()


def test_login_html_sets_only_nonce_cookie_and_uses_secure_redirect():
    rendered = csist._login_html(
        "https://apps.csiontario.ca/o/authorize/?state=opaque",
        "browser-nonce",
        "/content/app",
        True,
        auto_redirect=True,
    )
    assert f"{csist.COOKIE_NAME}=browser-nonce" in rendered
    assert "SameSite=Lax" in rendered
    assert "Secure" in rendered
    assert "access_token" not in rendered
    assert "window.location.replace" in rendered
    assert "\nstartLogin();\n" in rendered


def test_login_html_keeps_manual_retry_after_error_or_logout():
    rendered = csist._login_html(
        "https://apps.csiontario.ca/o/authorize/",
        "browser-nonce",
        "/",
        True,
        auto_redirect=False,
    )
    assert "Sign in with CSI" in rendered
    assert "\nstartLogin();\n" not in rendered


def test_logout_pauses_automatic_login(monkeypatch, streamlit_state):
    query, session, _ = streamlit_state
    query[csist.LOGOUT_PARAM] = "1"
    rendered = []
    monkeypatch.setattr(
        csist,
        "_require_production_config",
        lambda: (GOOD_KEY, REDIRECT, True, "/"),
    )
    monkeypatch.setattr(csist, "_finish_login", lambda *args: None)
    monkeypatch.setattr(
        csist, "_login_transaction", lambda *args: ("https://login.example/", "nonce")
    )
    monkeypatch.setattr(csist.st, "title", lambda *args: None)
    monkeypatch.setattr(csist.st, "write", lambda *args: None)
    monkeypatch.setattr(csist.st, "html", lambda value, **kwargs: rendered.append(value))
    monkeypatch.setattr(csist.st, "stop", lambda: (_ for _ in ()).throw(Stop()))

    with pytest.raises(Stop):
        csist._authenticate()

    assert session[csist.LOGIN_PAUSED_KEY] is True
    assert "\nstartLogin();\n" not in rendered[0]


def test_page_wrapper_authenticates_before_running_body(monkeypatch):
    calls = []
    monkeypatch.setattr(csist.st, "set_page_config", lambda **kwargs: calls.append("config"))
    monkeypatch.setattr(csist.st, "html", lambda value, **kwargs: calls.append("html"))
    monkeypatch.setattr(
        csist,
        "_authenticate",
        lambda: {"access_token": "token", "user": None},
    )
    csist.page_wrapper(lambda: calls.append("body"), sandbox=False)
    assert calls == ["config", "html", "body", "html"]


def test_page_wrapper_rejects_non_callable():
    with pytest.raises(TypeError, match="must be callable"):
        csist.page_wrapper("not callable")


def test_chrome_escapes_user_info():
    rendered = csist._chrome_html(
        False,
        {
            "user": {
                "first_name": "<script>alert(1)</script>",
                "last_name": "User",
            }
        },
    )
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert f"?{csist.LOGOUT_PARAM}=1" in rendered
