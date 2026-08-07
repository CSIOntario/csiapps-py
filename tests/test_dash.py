"""csiapps.dash: the auth guard, the OAuth callback routes, and the chrome.

The guard mechanics themselves (public routes, /_dash-update-component
handling) are dash-auth's tests, not ours; what is pinned here is the CSIAPPS
flow layered on top, the failure modes that would otherwise surface as a login
loop in production, and the sandbox behaviour that lets a Dash app be developed
with no credentials and no network.
"""

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

pytest.importorskip("dash", reason="csiapps[dash] not installed")
pytest.importorskip("dash_auth", reason="csiapps[dash] not installed")

import flask  # noqa: E402
from dash import Dash, dcc  # noqa: E402

import csiapps  # noqa: E402
from csiapps import auth, chrome, client, config  # noqa: E402
from csiapps import dash as csidash  # noqa: E402

GOOD_KEY = "k" * 48


# ---- fixtures ----------------------------------------------------------


@pytest.fixture
def prod_env(monkeypatch):
    monkeypatch.setenv("CSIAPPS_SECRET_KEY", GOOD_KEY)
    monkeypatch.setenv("CSIAPPS_CLIENT_ID", "test-client")
    monkeypatch.setenv("CSIAPPS_REDIRECT_URI", "https://app.example.ca/redirect")


def make_app(sandbox=None, public_routes=None, layout=True, **layout_kwargs):
    app = Dash(__name__)
    csidash.attach(app, public_routes=public_routes, sandbox=sandbox)
    if layout:
        app.layout = csidash.layout_wrapper(**layout_kwargs)
    return app


def render(**kwargs):
    """Render layout_wrapper outside a request context and return its HTML-ish repr."""
    return str(csidash.layout_wrapper(**kwargs)())


def begin_login(client_, path="/"):
    """Start the guarded login flow and return its session-bound OAuth state."""
    resp = client_.get(path)
    return parse_qs(urlparse(resp.headers["Location"]).query)["state"][0]


# ---- attach: mode selection --------------------------------------------


def test_sandbox_skips_the_guard():
    app = make_app(sandbox=True)
    assert app.server.test_client().get("/").status_code == 200


def test_sandbox_is_the_default_mode():
    # The package-wide fail-safe: no CSIAPPS_ENV set means sandbox, so a Dash app
    # cannot accidentally demand production credentials on first run.
    app = make_app()
    assert app.server.test_client().get("/").status_code == 200


def test_production_installs_the_guard(prod_env):
    resp = make_app(sandbox=False).server.test_client().get("/")
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith(config.auth_url())


def test_production_guard_protects_the_dash_routes(prod_env):
    client_ = make_app(sandbox=False).server.test_client()
    # The layout and callback endpoints must be behind the guard too, or an
    # unauthenticated user still gets data.
    assert client_.get("/_dash-layout").status_code == 302
    assert client_.get("/some/deep/page").status_code == 302


def test_env_var_selects_production(monkeypatch, prod_env):
    monkeypatch.setenv("CSIAPPS_ENV", "production")
    assert make_app().server.test_client().get("/").status_code == 302


def test_public_routes_bypass_the_guard(prod_env):
    app = make_app(sandbox=False, public_routes=["/health"])

    @app.server.route("/health")
    def _health():
        return "ok"

    client_ = app.server.test_client()
    assert client_.get("/health").status_code == 200
    assert client_.get("/").status_code == 302  # everything else still guarded


# ---- attach: the login redirect ----------------------------------------


def test_login_redirect_carries_pkce_and_client_config(prod_env):
    resp = make_app(sandbox=False).server.test_client().get("/")
    params = parse_qs(urlparse(resp.headers["Location"]).query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["test-client"]
    assert params["redirect_uri"] == ["https://app.example.ca/redirect"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"]  # present and non-empty
    assert params["scope"] == ["read write"]


def test_login_state_and_verifier_are_bound_to_the_session(prod_env):
    import base64
    import hashlib

    app = make_app(sandbox=False)
    with app.server.test_client() as c:
        resp = c.get("/")
        params = parse_qs(urlparse(resp.headers["Location"]).query)
        state = params["state"][0]
        verifier = flask.session[csidash.VERIFIER_KEY]
        assert flask.session[csidash.STATE_KEY] == state
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert params["code_challenge"][0] == expected


def test_login_redirect_remembers_where_the_user_was_going(prod_env):
    app = make_app(sandbox=False)
    with app.server.test_client() as c:
        c.get("/reports?year=2026")
        assert flask.session[csidash.NEXT_KEY].startswith("/reports")


def test_login_redirect_follows_the_institute(monkeypatch, prod_env):
    monkeypatch.setattr(config, "_state", dict(config._state, institute="csiontario"))
    resp = make_app(sandbox=False).server.test_client().get("/")
    assert resp.headers["Location"].startswith("https://apps.csiontario.ca/o/authorize/")


# ---- attach: secret key ------------------------------------------------


def test_missing_secret_key_raises(monkeypatch):
    monkeypatch.delenv("CSIAPPS_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="CSIAPPS_SECRET_KEY"):
        make_app(sandbox=False)


def test_short_secret_key_raises(monkeypatch):
    monkeypatch.setenv("CSIAPPS_SECRET_KEY", "tooshort")
    with pytest.raises(ValueError, match="at least 32"):
        make_app(sandbox=False)


def test_secret_key_is_installed_on_the_server(prod_env):
    assert make_app(sandbox=False).server.secret_key == GOOD_KEY


def test_sandbox_does_not_require_a_secret_key(monkeypatch):
    monkeypatch.delenv("CSIAPPS_SECRET_KEY", raising=False)
    make_app(sandbox=True)  # must not raise


# ---- attach: deployment hardening --------------------------------------


def test_production_sets_cookie_flags(prod_env):
    cfg = make_app(sandbox=False).server.config
    assert cfg["SESSION_COOKIE_SECURE"] is True
    assert cfg["SESSION_COOKIE_HTTPONLY"] is True
    # Lax, never Strict: Strict drops the cookie on the cross-site return from
    # the OAuth provider and the login loops forever.
    assert cfg["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_production_applies_proxyfix(prod_env):
    from werkzeug.middleware.proxy_fix import ProxyFix

    assert isinstance(make_app(sandbox=False).server.wsgi_app, ProxyFix)


def test_sandbox_leaves_the_wsgi_stack_alone():
    from werkzeug.middleware.proxy_fix import ProxyFix

    assert not isinstance(make_app(sandbox=True).server.wsgi_app, ProxyFix)


def test_attach_is_idempotent(prod_env):
    # Flask raises on a duplicate endpoint name and ProxyFix would stack, so a
    # second attach() must be a no-op rather than an error.
    from werkzeug.middleware.proxy_fix import ProxyFix

    app = Dash(__name__)
    csidash.attach(app, sandbox=False)
    csidash.attach(app, sandbox=False)
    csidash.attach(app, sandbox=False)
    app.layout = csidash.layout_wrapper()
    assert isinstance(app.server.wsgi_app, ProxyFix)
    assert not isinstance(app.server.wsgi_app.app, ProxyFix)
    assert app.server.test_client().get("/").status_code == 302


def test_attach_returns_the_app():
    app = Dash(__name__)
    assert csidash.attach(app, sandbox=True) is app


# ---- attach: multi-worker sandbox warning ------------------------------


def test_multiworker_sandbox_warns(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with pytest.warns(UserWarning, match="workers 1"):
        make_app(sandbox=True)


def test_single_worker_sandbox_is_silent(monkeypatch, recwarn):
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    make_app(sandbox=True)
    assert not [w for w in recwarn if "WEB_CONCURRENCY" in str(w.message)]


def test_unset_or_garbage_concurrency_is_silent(monkeypatch, recwarn):
    monkeypatch.setenv("WEB_CONCURRENCY", "not-a-number")
    make_app(sandbox=True)
    monkeypatch.delenv("WEB_CONCURRENCY")
    make_app(sandbox=True)
    assert not [w for w in recwarn if "WEB_CONCURRENCY" in str(w.message)]


def test_production_does_not_warn_about_workers(monkeypatch, prod_env, recwarn):
    # Production genuinely is worker-count-agnostic: no production path writes to
    # sandbox._state and the token lives in the client's cookie.
    monkeypatch.setenv("WEB_CONCURRENCY", "8")
    make_app(sandbox=False)
    assert not [w for w in recwarn if "WEB_CONCURRENCY" in str(w.message)]


# ---- the OAuth callback route ------------------------------------------


@pytest.fixture
def logged_in(monkeypatch, prod_env):
    """A prod app whose token exchange and /me are stubbed."""

    # Sentinel, not None: "userinfo=None" is itself a case under test (a /me call
    # that failed), and must be distinguishable from "not overridden".
    unset = object()

    def _build(token_response=unset, userinfo=unset):
        token = {"access_token": "granted-tok"} if token_response is unset else token_response
        me = {"first_name": "Ada", "last_name": "Lovelace"} if userinfo is unset else userinfo
        monkeypatch.setattr(auth, "exchange_code_for_token", lambda code, verifier=None: token)
        monkeypatch.setattr(csidash, "_load_userinfo", lambda tok: me)
        return make_app(sandbox=False)

    return _build


def test_callback_stores_token_and_user(logged_in):
    app = logged_in()
    with app.server.test_client() as c:
        state = begin_login(c)
        resp = c.get(f"/redirect?code=abc&state={state}")
        assert resp.status_code == 302
        assert flask.session[csidash.TOKEN_KEY] == "granted-tok"
        assert flask.session[csidash.USER_KEY]["first_name"] == "Ada"


def test_callback_passes_the_verifier_to_the_exchange(monkeypatch, prod_env):
    seen = {}
    monkeypatch.setattr(
        auth,
        "exchange_code_for_token",
        lambda code, verifier=None: seen.update(code=code, verifier=verifier)
        or {"access_token": "t"},
    )
    monkeypatch.setattr(csidash, "_load_userinfo", lambda tok: None)
    app = make_app(sandbox=False)
    with app.server.test_client() as c:
        state = begin_login(c)
        verifier = flask.session[csidash.VERIFIER_KEY]
        c.get(f"/redirect?code=thecode&state={state}")
    assert seen == {"code": "thecode", "verifier": verifier}


def test_callback_returns_the_user_to_their_original_page(logged_in):
    app = logged_in()
    with app.server.test_client() as c:
        state = begin_login(c, "/reports?year=2026")
        resp = c.get(f"/redirect?code=abc&state={state}")
        assert resp.headers["Location"].startswith("/reports")


def test_callback_defaults_to_root_without_a_stored_next(logged_in):
    app = logged_in()
    with app.server.test_client() as c:
        state = begin_login(c)
        flask.session.pop(csidash.NEXT_KEY)
        resp = c.get(f"/redirect?code=abc&state={state}")
        assert resp.headers["Location"] == "/"


def test_callback_survives_a_failed_userinfo(logged_in):
    # A stale or scope-limited token must still log the user in; only the name
    # is lost. Mirrors server_wrapper's degrade-gracefully behaviour.
    app = logged_in(userinfo=None)
    with app.server.test_client() as c:
        state = begin_login(c)
        c.get(f"/redirect?code=abc&state={state}")
        assert flask.session[csidash.TOKEN_KEY] == "granted-tok"
        assert csidash.USER_KEY not in flask.session


@pytest.mark.parametrize(
    "query,label",
    [
        ("error=access_denied", "provider returned an error"),
        ("", "no code at all"),
    ],
)
def test_callback_failure_modes_do_not_500(monkeypatch, prod_env, query, label):
    # Every one of these is reachable by editing a URL. None may crash, and none
    # may leave a token behind.
    monkeypatch.setattr(
        auth, "exchange_code_for_token", lambda code, verifier=None: {"error": "invalid_grant"}
    )
    monkeypatch.setattr(csidash, "_load_userinfo", lambda tok: None)
    app = make_app(sandbox=False)
    with app.server.test_client() as c:
        begin_login(c)
        resp = c.get(f"/redirect?{query}")
        assert resp.status_code == 302, label
        assert resp.headers["Location"] == "/", label
        assert csidash.TOKEN_KEY not in flask.session, label


def test_callback_rejects_missing_mismatched_or_replayed_state(monkeypatch, prod_env):
    exchanges = []
    monkeypatch.setattr(
        auth,
        "exchange_code_for_token",
        lambda code, verifier=None: exchanges.append((code, verifier))
        or {"access_token": "granted-tok"},
    )
    monkeypatch.setattr(csidash, "_load_userinfo", lambda tok: None)
    app = make_app(sandbox=False)

    with app.server.test_client() as c:
        state = begin_login(c)
        resp = c.get("/redirect?code=abc")
        assert resp.status_code == 302
        assert exchanges == []

        state = begin_login(c)
        resp = c.get("/redirect?code=abc&state=wrong")
        assert resp.status_code == 302
        assert exchanges == []

        state = begin_login(c)
        c.get(f"/redirect?code=abc&state={state}")
        assert len(exchanges) == 1
        c.get(f"/redirect?code=abc&state={state}")
        assert len(exchanges) == 1
        assert csidash.TOKEN_KEY not in flask.session


def test_callback_survives_a_failed_exchange(monkeypatch, prod_env):
    monkeypatch.setattr(
        auth, "exchange_code_for_token", lambda code, verifier=None: {"error": "invalid_grant"}
    )
    app = make_app(sandbox=False)
    with app.server.test_client() as c:
        state = begin_login(c)
        resp = c.get(f"/redirect?code=abc&state={state}")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"
        assert csidash.TOKEN_KEY not in flask.session


def test_logout_clears_the_session(logged_in):
    app = logged_in()
    with app.server.test_client() as c:
        state = begin_login(c)
        c.get(f"/redirect?code=abc&state={state}")
        assert csidash.TOKEN_KEY in flask.session
        resp = c.get("/csi-auth/logout")
        assert resp.status_code == 302
        assert csidash.TOKEN_KEY not in flask.session
        assert csidash.USER_KEY not in flask.session


def test_authenticated_user_reaches_the_app(logged_in):
    app = logged_in()
    with app.server.test_client() as c:
        state = begin_login(c)
        c.get(f"/redirect?code=abc&state={state}")
        assert c.get("/").status_code == 200  # guard now lets them through


def test_logout_then_guard_bounces_again(logged_in):
    app = logged_in()
    with app.server.test_client() as c:
        state = begin_login(c)
        c.get(f"/redirect?code=abc&state={state}")
        c.get("/csi-auth/logout")
        assert c.get("/").status_code == 302


# ---- the chrome CSS route ----------------------------------------------


def test_css_route_serves_the_shared_stylesheet():
    resp = make_app(sandbox=True).server.test_client().get(csidash.CSS_ROUTE)
    assert resp.status_code == 200
    assert resp.mimetype == "text/css"
    assert resp.get_data(as_text=True) == chrome.CHROME_CSS


def test_css_route_is_public_in_production(prod_env):
    # Served from under /csi-auth/, so the guard lets it through: an unstyled
    # login bounce would otherwise flash unbranded content.
    resp = make_app(sandbox=False).server.test_client().get(csidash.CSS_ROUTE)
    assert resp.status_code == 200


def test_css_carries_the_logo_height_pin():
    # Regression guard for 403d0e2: without this an app's own `img {}` rule
    # (Bootstrap, any theme) resizes the logo through the red accent line.
    css = make_app(sandbox=True).server.test_client().get(csidash.CSS_ROUTE).get_data(as_text=True)
    assert "height: 48px !important" in css
    assert "max-width: none !important" in css


# ---- layout_wrapper ----------------------------------------------------


def test_layout_wrapper_returns_a_per_request_callable():
    # Not a component: Dash calls it on every page load, which is what makes the
    # signed-in name reflect the current user rather than the first one.
    layout = csidash.layout_wrapper()
    assert callable(layout)
    assert layout() is not layout()  # a fresh tree each call


def test_layout_wrapper_renders_outside_a_request_context():
    # Apps assign app.layout at import time; touching flask.session there would
    # raise. Must degrade to the anonymous rendering instead.
    assert "csi-navbar" in render(sandbox=True)


def test_layout_renders_chrome_and_children():
    from dash import html

    out = str(csidash.layout_wrapper(html.H3("Athletes"), sandbox=True)())
    assert "csi-navbar" in out
    assert "footer" in out
    assert "Athletes" in out


def test_layout_links_the_served_stylesheet():
    assert csidash.CSS_ROUTE in render(sandbox=True)


def test_banner_shows_only_in_sandbox():
    assert chrome.SANDBOX_BANNER_TEXT in render(sandbox=True)
    assert chrome.SANDBOX_BANNER_TEXT not in render(sandbox=False)


def test_banner_style_is_a_react_style_dict():
    # Dash needs camelCased keys in a dict; the shared constant is CSS text.
    style = csidash._sandbox_banner().style
    assert style["fontSize"] == "12px"
    assert style["letterSpacing"] == ".02em"
    assert style["background"] == "#faf6ec"
    assert not any("-" in k for k in style)


def test_logo_follows_the_institute():
    csiapps.set_institute("csiontario")
    assert "logo-csi-ontario" in render(sandbox=True)
    csiapps.set_institute("csipacific")
    assert "csi-pacific-logo" in render(sandbox=True)


def test_footer_shows_the_institute_and_year():
    from datetime import date

    csiapps.set_institute("csiontario")
    out = render(sandbox=True)
    assert "CSI Ontario" in out
    assert str(date.today().year) in out


def test_nav_links_are_rendered():
    out = render(sandbox=True, nav_links=[{"label": "Reports", "href": "/reports"}])
    assert "Reports" in out
    assert "/reports" in out


def test_logout_link_only_outside_sandbox():
    # In sandbox there is no session to clear and the route is not registered, so
    # offering the link would be a dead end.
    assert csidash.LOGOUT_ROUTE in render(sandbox=False)
    assert csidash.LOGOUT_ROUTE not in render(sandbox=True)


def test_auth_status_unauthenticated_in_bare_sandbox(monkeypatch):
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    assert chrome.UNAUTHENTICATED_TEXT in render(sandbox=True)


def test_auth_status_signed_in_with_a_dev_token(monkeypatch):
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "dev-tok")
    out = render(sandbox=True)
    assert "Signed in (sandbox)" in out
    assert chrome.UNAUTHENTICATED_TEXT not in out


def test_auth_status_names_the_user_from_the_session(logged_in):
    app = logged_in()
    with app.server.test_client() as c:
        state = begin_login(c)
        c.get(f"/redirect?code=abc&state={state}")
        # Inside the request context the layout sees the stored /me payload.
        assert "Signed in as Ada Lovelace" in str(csidash.layout_wrapper(sandbox=False)())


# ---- the shapes the target app depends on ------------------------------


def test_dropdown_accepts_fetch_org_options_directly():
    # Pins the load-bearing dash>=2.1 dict-options form. fetch_org_options
    # returns {value: label} because that is Shiny's choices= shape; Dash
    # happening to accept the identical dict is what avoids a shape= argument.
    csiapps.create_sport_org("Rowing", id=7)
    options = csiapps.fetch_org_options()
    assert options == {7: "Rowing"}
    assert dcc.Dropdown(id="org", options=options).options == {7: "Rowing"}


@pytest.mark.filterwarnings("ignore:.*dash_table.*:DeprecationWarning")
def test_datatable_accepts_flattened_profiles():
    # dash_table is soft-deprecated in Dash 4 in favour of dash-ag-grid, but it
    # is what both CSI Dash apps use today, so the shape compatibility is worth
    # pinning until they migrate.
    from dash import dash_table

    csiapps.create_sport_org("Rowing", id=7)
    csiapps.create_profile(2, 7)
    rows = [csiapps.flatten_profile(p) for p in csiapps.fetch_profiles()]
    # No pandas needed, unlike Shiny's render.data_frame.
    assert dash_table.DataTable(id="t", data=rows).data == rows
    assert json.dumps(rows)  # JSON-serialisable, so it survives the wire


def test_sandbox_data_flows_end_to_end_through_a_dash_app():
    # The capability neither Dash repo has today: a full app with no
    # credentials, no network and no token.
    csiapps.create_sport_org("Swim BC", id=200)
    csiapps.create_profile(3, 200)
    app = make_app(sandbox=True)
    with app.server.test_client() as c:
        assert c.get("/").status_code == 200
        with app.server.test_request_context("/"):
            profiles = csiapps.fetch_profiles(filters={"sport_org_id": 200})
    assert len(profiles) == 3


# ---- the Flask token adapter -------------------------------------------
#
# csiapps.dash registers a token adapter on import so client.current_token()
# reads session["csi_token"] without the core importing Flask. This is the one
# seam back into the framework-neutral core, so its behaviour (inert outside a
# request, per-request token outranks the env var, degrades on a bad cookie) is
# pinned here rather than in the core token test.

SITE = "https://apps.csipacific.ca"


@pytest.fixture
def flask_app():
    app = flask.Flask(__name__)
    app.secret_key = "k" * 48
    return app


def test_flask_token_wins_over_env(monkeypatch, flask_app):
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    with flask_app.test_request_context("/"):
        flask.session[csidash.TOKEN_KEY] = "flasktok"
        assert client.current_token() == "flasktok"


def test_flask_adapter_is_inert_outside_a_request(monkeypatch):
    # A non-Dash process (or a Dash app between requests) must fall through to the
    # env var exactly as if no adapter were installed.
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    assert csidash._FlaskTokenAdapter().read_token() is None
    assert client.current_token() == "envtok"


def test_empty_flask_token_falls_through_to_env(monkeypatch, flask_app):
    # An empty string in the cookie is not a token; it must not mask the env var.
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    with flask_app.test_request_context("/"):
        flask.session[csidash.TOKEN_KEY] = ""
        assert client.current_token() == "envtok"


def test_no_token_in_a_request_is_empty_string(monkeypatch, flask_app):
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    with flask_app.test_request_context("/"):
        assert client.current_token() == ""
        assert client.token_ready() is False


def test_unreadable_session_does_not_propagate(monkeypatch):
    # A tampered or undecryptable cookie must degrade to "no token", not take the
    # whole request down with a 500.
    class Boom:
        def get(self, key):
            raise RuntimeError("bad session cookie")

    monkeypatch.setattr(csidash, "has_request_context", lambda: True)
    monkeypatch.setattr(csidash, "session", Boom())
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    assert csidash._FlaskTokenAdapter().read_token() is None
    assert client.current_token() == "envtok"


@respx.mock
def test_make_request_uses_the_flask_token(monkeypatch, flask_app):
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    route = respx.get(f"{SITE}/api/csiauth/me/").mock(
        return_value=httpx.Response(200, json={"first_name": "Ada"})
    )
    with flask_app.test_request_context("/"):
        flask.session[csidash.TOKEN_KEY] = "flasktok"
        out = client.make_request("api/csiauth/me/", sandbox=False)
    assert out == {"first_name": "Ada"}
    assert route.calls.last.request.headers["Authorization"] == "Bearer flasktok"


def test_missing_token_in_a_request_raises_loudly(monkeypatch, flask_app):
    # Behind attach()'s guard this is unreachable for a real user, which makes a
    # missing token here the right signal for a misconfiguration: raise, don't
    # silently gate (Dash has no reactive to cancel).
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    with flask_app.test_request_context("/"):
        with pytest.raises(RuntimeError, match="no CSIAPPS_ACCESS_TOKEN set"):
            client.make_request("api/csiauth/me/", sandbox=False)
