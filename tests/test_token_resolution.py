"""The cross-framework token seam in client.current_token().

This is the only place csiapps.dash reaches into the framework-independent core,
so it is the one change that could plausibly break a working Shiny app. These
tests pin the resolution order (Shiny session > Flask session > env var), that
each provider is inert when its framework is absent or idle, and that the Flask
probe is resolved once rather than on every call.
"""

import httpx
import pytest
import respx
from shiny import reactive

from csiapps import client
from csiapps.client import current_token, make_request, token_ready

flask = pytest.importorskip("flask", reason="csiapps[dash] not installed")

SITE = "https://apps.csipacific.ca"


class FakeSession:
    """Weak-referenceable stand-in for a Shiny session (as in test_client.py)."""


@pytest.fixture
def flask_app():
    app = flask.Flask(__name__)
    app.secret_key = "k" * 48
    return app


# ---- resolution order --------------------------------------------------


def test_flask_token_wins_over_env(monkeypatch, flask_app):
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    with flask_app.test_request_context("/"):
        flask.session[client.FLASK_TOKEN_KEY] = "flasktok"
        assert current_token() == "flasktok"


def test_flask_provider_is_inert_outside_a_request(monkeypatch):
    # The critical no-regression case: a Shiny process has Flask importable (it
    # is a transitive dep of plenty of things) but never a request context, so
    # the env var must still win exactly as before.
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    assert client._get_flask_token() is None
    assert current_token() == "envtok"


def test_shiny_session_outranks_flask_session(monkeypatch, flask_app):
    # Nothing should ever be both at once, but if it is, the per-session Shiny
    # token is the more specific answer and must not be shadowed.
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    sess = FakeSession()
    monkeypatch.setattr(client, "_get_current_session", lambda: sess)
    client.set_session_token(sess, "shinytok")
    with flask_app.test_request_context("/"):
        flask.session[client.FLASK_TOKEN_KEY] = "flasktok"
        assert current_token() == "shinytok"


def test_flask_fills_in_when_shiny_session_has_no_token(monkeypatch, flask_app):
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    sess = FakeSession()
    monkeypatch.setattr(client, "_get_current_session", lambda: sess)
    client.set_session_token(sess, None)
    with flask_app.test_request_context("/"):
        flask.session[client.FLASK_TOKEN_KEY] = "flasktok"
        assert current_token() == "flasktok"


def test_empty_flask_token_falls_through_to_env(monkeypatch, flask_app):
    # An empty string in the cookie is not a token; it must not mask the env var.
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    with flask_app.test_request_context("/"):
        flask.session[client.FLASK_TOKEN_KEY] = ""
        assert current_token() == "envtok"


def test_no_token_anywhere_is_empty_string(monkeypatch, flask_app):
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    with flask_app.test_request_context("/"):
        assert current_token() == ""
        assert token_ready() is False


def test_token_ready_true_inside_flask_request(monkeypatch, flask_app):
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    with flask_app.test_request_context("/"):
        flask.session[client.FLASK_TOKEN_KEY] = "flasktok"
        assert token_ready() is True


# ---- degradation -------------------------------------------------------


def test_flask_absent_is_handled_and_cached(monkeypatch):
    # Simulate an install without the dash extra: the probe must yield None and
    # must not be retried, since a *failed* import is not cached by Python and
    # re-walks sys.path (~20x a cached import) on every call.
    monkeypatch.setattr(client, "_flask_accessors", None)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def _no_flask(name, *args, **kwargs):
        if name == "flask":
            raise ImportError("No module named 'flask'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _no_flask)
    assert client._get_flask_token() is None
    assert client._flask_accessors is False  # resolved, not retried

    # Second call must not re-attempt the import at all.
    def _explode(name, *args, **kwargs):
        if name == "flask":
            raise AssertionError("import retried; the probe is not cached")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _explode)
    assert client._get_flask_token() is None


def test_probe_is_resolved_once_when_flask_is_present(monkeypatch):
    monkeypatch.setattr(client, "_flask_accessors", None)
    client._get_flask_token()
    first = client._flask_accessors
    assert first is not None and first is not False
    client._get_flask_token()
    assert client._flask_accessors is first


def test_unreadable_session_does_not_propagate(monkeypatch):
    # A tampered or undecryptable cookie must degrade to "no token", not take the
    # whole request down with a 500.
    class Boom:
        def get(self, key):
            raise RuntimeError("bad session cookie")

    monkeypatch.setattr(client, "_flask_accessors", (lambda: True, Boom()))
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    assert client._get_flask_token() is None
    assert current_token() == "envtok"


# ---- end to end --------------------------------------------------------


@respx.mock
def test_make_request_uses_the_flask_token(monkeypatch, flask_app):
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    route = respx.get(f"{SITE}/api/csiauth/me/").mock(
        return_value=httpx.Response(200, json={"first_name": "Ada"})
    )
    with flask_app.test_request_context("/"):
        flask.session[client.FLASK_TOKEN_KEY] = "flasktok"
        out = make_request("api/csiauth/me/", sandbox=False)
    assert out == {"first_name": "Ada"}
    assert route.calls.last.request.headers["Authorization"] == "Bearer flasktok"


def test_missing_token_in_a_flask_request_raises_loudly(monkeypatch, flask_app):
    # Dash has no reactive context to cancel, so _auth_gate must raise. Behind
    # attach()'s guard this is unreachable for a real user, which makes it the
    # right signal for a misconfiguration.
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    with flask_app.test_request_context("/"):
        with pytest.raises(RuntimeError, match="no CSIAPPS_ACCESS_TOKEN set"):
            make_request("api/csiauth/me/", sandbox=False)


def test_shiny_reactive_gating_still_works_with_flask_installed(monkeypatch):
    # The regression that would hurt most: the pre-login quiet-gating path from
    # test_client.py must behave identically now that a second provider sits
    # between the session and the env var.
    import asyncio

    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    sess = FakeSession()
    monkeypatch.setattr(client, "_get_current_session", lambda: sess)
    client.set_session_token(sess, None)

    state = {"runs": 0, "reached_api": 0}

    @reactive.effect
    def _consumer():
        state["runs"] += 1
        client.fetch_org_options(sandbox=False)
        state["reached_api"] += 1

    async def drive():
        await reactive.flush()
        assert (state["runs"], state["reached_api"]) == (1, 0)
        client.set_session_token(sess, "tok-abc")
        await reactive.flush()
        assert (state["runs"], state["reached_api"]) == (2, 1)

    with respx.mock:
        respx.get(f"{SITE}/api/registration/organization/").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        asyncio.run(drive())

    _consumer.destroy()
