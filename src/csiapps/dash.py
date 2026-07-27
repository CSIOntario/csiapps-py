"""Dash wrappers for CSIAPPS apps.

The Dash counterpart of :mod:`csiapps.shiny`: the same three things the Shiny
wrapper provides — authentication, ambient data access, and the CSI chrome —
behind the same vocabulary.

    from csiapps.dash import attach, layout_wrapper

``attach(app)`` installs a ``before_request`` guard (via ``dash-auth``) that
runs the OAuth2 PKCE flow before Dash renders anything, and stores the resulting
token in the Flask session, where :func:`csiapps.client.current_token` picks it
up. Callbacks then call ``fetch_profiles()`` and friends with no token handling
at all, exactly as in Shiny.

Framework mapping notes (Shiny for Python -> Dash):

* ``server_wrapper``'s reactive OAuth effect -> a ``before_request`` guard plus
  two Flask routes. The redirect happens before Dash renders, so there is no
  ``dcc.Interval``/``dcc.Location`` login bounce.
* ``session$userData`` / ``client.set_session_token`` -> the Flask session
  cookie, read back by the same :func:`csiapps.client.current_token`.
* ``req(False)`` gating has no Dash analogue and needs none: behind the guard an
  unauthenticated user never reaches a callback, so ``_auth_gate``'s
  ``RuntimeError`` is the correct (and loud) outcome for a real misconfiguration.
* ``ui_wrapper`` -> :func:`layout_wrapper`, rendering the identical CSS and copy
  from :mod:`csiapps.chrome`.

Everything else — ``make_request``, the ``fetch_*`` helpers, the sandbox, the
PKCE helpers — is framework-independent and used unchanged.
"""

import os
import warnings
from urllib.parse import urlencode

from . import auth, chrome, client, config

try:
    import httpx
    from dash import html
    from dash_auth.auth import Auth as _DashAuth
    from flask import Response, has_request_context, redirect, request, session
    from werkzeug.middleware.proxy_fix import ProxyFix
except ImportError as exc:  # pragma: no cover - exercised by the no-extra CI job
    raise ImportError(
        "csiapps.dash requires the optional Dash dependencies, which are not "
        "installed. Install them with:\n\n"
        "    pip install 'csiapps[dash]'\n\n"
        f"(missing: {exc.name})"
    ) from exc

# All csiapps-owned routes live under this prefix: it is the one path the auth
# guard lets through unauthenticated, so the login callback can complete.
AUTH_PREFIX = "/csi-auth"
REDIRECT_ROUTE = f"{AUTH_PREFIX}/redirect"
LOGOUT_ROUTE = f"{AUTH_PREFIX}/logout"
CSS_ROUTE = f"{AUTH_PREFIX}/chrome.css"

# Flask session keys. The token key is defined here (this module owns the Flask
# side of token storage) and read back through the adapter below.
FLASK_TOKEN_KEY = "csi_token"
TOKEN_KEY = FLASK_TOKEN_KEY
USER_KEY = "csi_user"
NEXT_KEY = "csi_next"


class _FlaskTokenAdapter:
    """Reads ``session['csi_token']`` for :func:`csiapps.client.current_token`.

    Returns ``None`` outside a request context, so it is inert in any non-Dash
    process. A missing token is never handled here: behind :func:`attach`'s guard
    an unauthenticated user never reaches a callback, so a missing token is a
    real misconfiguration and the core should raise loudly.
    """

    def read_token(self):
        if not has_request_context():
            return None
        try:
            return session.get(FLASK_TOKEN_KEY) or None
        except Exception:
            # An unreadable/tampered session cookie must not take the app down;
            # fall through to the env var and the normal unauthenticated gate.
            return None

    def handle_missing_token(self) -> bool:
        return False


client.register_token_adapter(_FlaskTokenAdapter())

# Flask refuses to register two view functions on one endpoint name, and
# ProxyFix would stack if applied twice, so attach() is made idempotent with a
# marker on the Flask app rather than by catching the resulting errors.
_ATTACHED_FLAG = "_csiapps_attached"

MIN_SECRET_KEY_BYTES = 32


# ---- helpers -----------------------------------------------------------


def _current_user():
    """The ``/me`` payload for the current request, or ``None``.

    Not exported: the signed-in name is chrome, and an app that needs identity
    should read it from the API rather than from a cookie.
    """
    if not has_request_context():
        return None
    try:
        return session.get(USER_KEY)
    except Exception:
        return None


def _load_userinfo(access_token):
    # Best-effort: a stale or scope-limited token still logs the user in, it just
    # shows a bare "Signed in". Mirrors server_wrapper's degrade-gracefully path.
    try:
        resp = httpx.get(
            config.userinfo_url(),
            headers={"Authorization": f"Bearer {access_token}"},
            follow_redirects=True,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _warn_if_multiworker():
    # sandbox._state is process-global, so a multi-worker sandbox deployment
    # serves a different dummy roster from each worker and loses records written
    # to one. Best-effort detection, hence a warning rather than a raise: a
    # single-worker sandbox is entirely legitimate.
    raw = os.environ.get("WEB_CONCURRENCY", "")
    try:
        workers = int(raw)
    except ValueError:
        return
    if workers > 1:
        warnings.warn(
            f"csiapps.dash: sandbox mode with WEB_CONCURRENCY={workers}. The sandbox "
            "registry is per-process, so each worker generates its own dummy athletes "
            "and ingested records are invisible to the other workers. Deploy sandbox "
            "apps with --workers 1.",
            stacklevel=3,
        )


def _require_secret_key(server):
    # Without a stable key across processes and restarts the session cookie is
    # unreadable and users re-login constantly -- the failure mode is a login
    # loop in production, so fail at startup instead.
    key = os.environ.get("CSIAPPS_SECRET_KEY", "")
    if len(key) < MIN_SECRET_KEY_BYTES:
        raise ValueError(
            "csiapps.dash.attach: CSIAPPS_SECRET_KEY must be set to at least "
            f"{MIN_SECRET_KEY_BYTES} characters outside sandbox mode; got "
            f"{len(key)}. It signs the session cookie holding the access token, "
            "and it must be stable across restarts and workers. Generate one "
            "with:  python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )
    server.secret_key = key


def _configure_cookies(server):
    server.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        # Lax, not Strict: Strict drops the cookie on the cross-site return from
        # the OAuth provider, and the login loops forever.
        SESSION_COOKIE_SAMESITE="Lax",
    )


# ---- auth --------------------------------------------------------------


class _CsiAuth(_DashAuth):
    """``dash-auth`` guard running the CSIAPPS OAuth2 PKCE flow.

    ``dash_auth.Auth`` supplies the ``before_request`` hook, the public-route
    matching and the ``/_dash-update-component`` handling; only these two hooks
    are ours. ``dash_auth.OIDCAuth`` is deliberately not used: it reads identity
    from an OIDC ``userinfo`` claim, which a plain OAuth2 provider does not
    supply, and it would pull ``authlib`` in alongside the PKCE helpers this
    package already has and tests.
    """

    def is_authorized(self):
        # Own routes are always reachable: the redirect route is how a session
        # acquires a token in the first place.
        if request.path.startswith(AUTH_PREFIX + "/"):
            return True
        return bool(session.get(TOKEN_KEY))

    def login_request(self):
        pkce = auth.generate_pkce()
        # Remember where the user was headed so the redirect route can return
        # them there instead of dumping everyone on "/".
        session[NEXT_KEY] = request.full_path
        params = {
            "response_type": "code",
            "client_id": os.environ.get("CSIAPPS_CLIENT_ID", ""),
            "redirect_uri": os.environ.get("CSIAPPS_REDIRECT_URI", ""),
            "scope": os.environ.get("CSIAPPS_SCOPE", "read write"),
            "code_challenge": pkce["challenge"],
            "code_challenge_method": pkce["method"],
            "state": auth.pkce_state_encode(pkce["verifier"]),
        }
        return redirect(config.auth_url() + "?" + urlencode(params))


def _register_css_route(server):
    # Served rather than inlined: Dash's html module has no Style component, and
    # the alternatives (index_string surgery, a data: URI that a strict CSP
    # blocks, an assets folder) all cost more than one four-line route.
    @server.route(CSS_ROUTE)
    def _csiapps_chrome_css():
        resp = Response(chrome.CHROME_CSS, mimetype="text/css")
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp


def _register_auth_routes(server):
    @server.route(REDIRECT_ROUTE)
    def _csiapps_oauth_redirect():
        if request.args.get("error"):
            session.clear()
            return redirect("/")

        code = request.args.get("code")
        if not code:
            session.clear()
            return redirect("/")

        state = request.args.get("state")
        verifier = None
        if state:
            try:
                verifier = auth.pkce_state_decode(state).get("v")
            except Exception:
                # A malformed/tampered state is not a crash: drop the session and
                # restart the flow from the guard.
                session.clear()
                return redirect("/")

        token = auth.exchange_code_for_token(code, verifier)
        access_token = token.get("access_token") if isinstance(token, dict) else None
        if not access_token:
            session.clear()
            return redirect("/")

        # Only the access token is stored, not the whole response: the session
        # cookie has a ~4KB budget and refresh handling is out of scope (see
        # DASH_PACKAGE_PLAN.md sections 11 and 12.2).
        session[TOKEN_KEY] = access_token
        userinfo = _load_userinfo(access_token)
        if userinfo:
            session[USER_KEY] = {
                "first_name": userinfo.get("first_name"),
                "last_name": userinfo.get("last_name"),
            }
        return redirect(session.pop(NEXT_KEY, None) or "/")

    @server.route(LOGOUT_ROUTE)
    def _csiapps_logout():
        session.clear()
        return redirect("/")


def attach(app, public_routes=None, sandbox=None):
    """Install CSIAPPS authentication and chrome support on a Dash app.

    Call this once, immediately after constructing the app and before assigning
    the layout. In production it installs a ``before_request`` guard that
    redirects unauthenticated users through the CSIAPPS OAuth2 PKCE flow, so no
    callback ever runs without a token. In sandbox mode the guard is skipped
    entirely and the app runs with no credentials and no network — the same
    fail-safe default the rest of the package uses.

    Args:
        app: The ``dash.Dash`` application to protect.
        public_routes: Flask-syntax routes reachable without logging in, e.g.
            ``["/health", "/docs/<page>"]``. ``None`` (the default) protects
            everything. csiapps' own ``/csi-auth/*`` routes are always public.
        sandbox: Force sandbox (``True``) or production (``False``) behaviour.
            ``None`` (the default) resolves via
            [`is_sandbox_mode`][csiapps.config.is_sandbox_mode].

    Returns:
        The same ``app``, so the call can be chained.

    Raises:
        ValueError: In production mode only, if ``CSIAPPS_SECRET_KEY`` is missing
            or shorter than 32 characters. It signs the session cookie holding
            the access token; without a stable value across restarts and workers
            users are thrown into a login loop.

    Warns:
        UserWarning: In sandbox mode when ``WEB_CONCURRENCY`` is greater than 1.
            The sandbox registry is per-process, so each worker invents its own
            dummy athletes and ingested records are invisible to the others.
            Deploy sandbox apps with ``--workers 1``.

    Example:
        ```python
        from dash import Dash
        from csiapps.dash import attach, layout_wrapper

        app = Dash(__name__)
        attach(app)
        app.layout = layout_wrapper(...)
        ```

    Note:
        Calling this twice on the same app is a no-op — the second call returns
        immediately rather than double-registering routes or stacking
        ``ProxyFix``. In production it also sets ``Secure``/``HttpOnly``/
        ``SameSite=Lax`` on the session cookie and applies ``ProxyFix``, since
        Posit Connect Cloud terminates TLS upstream.
    """
    server = app.server
    if getattr(server, _ATTACHED_FLAG, False):
        return app
    setattr(server, _ATTACHED_FLAG, True)

    # Registered in both modes: layout_wrapper always links to it, and a sandbox
    # app must render with the same chrome as a production one.
    _register_css_route(server)

    if sandbox is None:
        sandbox = config.is_sandbox_mode()

    if sandbox:
        _warn_if_multiworker()
        return app

    _require_secret_key(server)
    _configure_cookies(server)
    # Connect Cloud terminates TLS upstream; without this the scheme is wrong and
    # the redirect_uri built from the request would be http://.
    server.wsgi_app = ProxyFix(server.wsgi_app, x_for=1, x_proto=1, x_host=1)
    _register_auth_routes(server)
    _CsiAuth(app, public_routes=public_routes)
    return app


# ---- chrome ------------------------------------------------------------


def _navbar(nav_links, sandbox):
    brand = html.A(
        html.Img(
            src=chrome.logo_src(),
            height=chrome.LOGO_HEIGHT,
            style={"marginRight": "8px"},
        ),
        className="navbar-brand d-flex align-items-center",
        href="#",
    )

    items = [
        html.Li(
            html.A(link["label"], href=link["href"], className="nav-link"),
            className="nav-item",
        )
        for link in (nav_links or [])
    ]
    # No logout link in sandbox mode: there is no session to clear, and the route
    # is not registered.
    if not sandbox:
        items.append(
            html.Li(
                html.A("Log out", href=LOGOUT_ROUTE, className="nav-link"),
                className="nav-item",
            )
        )

    return html.Nav(
        html.Div(
            [brand, html.Ul(items, className="navbar-nav ms-auto")],
            className="container-fluid",
        ),
        id="csi-navbar",
        className="navbar navbar-expand-lg navbar-light bg-white px-3",
    )


def _footer():
    return html.Footer(
        html.Div(
            [
                html.P(chrome.footer_text(), className="col-md-4 mb-0"),
                html.Ul(className="nav col-md-4 justify-content-end"),
            ],
            className=(
                "d-flex flex-wrap justify-content-between align-items-center py-3 container"
            ),
        ),
        id="footer",
        className="mt-4 bg-dark text-white border-top border-light fixed-bottom",
    )


def _sandbox_banner():
    # style= is a dict in Dash; the shared CSS-text constant is parsed rather
    # than duplicated so the two frameworks cannot drift apart.
    style = {}
    for decl in chrome.SANDBOX_BANNER_STYLE.split(";"):
        if ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        prop = prop.strip()
        # css-prop -> cssProp, which is what React expects.
        head, *rest = prop.split("-")
        style[head + "".join(w.title() for w in rest)] = value.strip()
    return html.Div(
        chrome.SANDBOX_BANNER_TEXT,
        className=chrome.SANDBOX_BANNER_CLASS,
        style=style,
    )


def _auth_status(sandbox):
    if sandbox and not auth.seed_sandbox_token().get("access_token"):
        return html.P(chrome.UNAUTHENTICATED_TEXT)
    return html.P(chrome.signed_in_text(_current_user(), sandbox))


def layout_wrapper(*children, nav_links=None, sandbox=None):
    """Wrap an app's layout in the standard CSI chrome.

    The Dash counterpart of [`ui_wrapper`][csiapps.shiny.ui_wrapper]: same navbar,
    footer, auth-status line and sandbox banner, rendered from the same
    constants in `csiapps.chrome`.

    Args:
        *children: The app's own Dash components, rendered below the
            auth-status line inside the chrome.
        nav_links: Optional navbar links as
            ``[{"label": ..., "href": ...}, ...]`` — useful for ``use_pages``
            apps. A "Log out" link is appended automatically outside sandbox
            mode.
        sandbox: Force the sandbox banner on (``True``) or off (``False``).
            ``None`` (the default) resolves via
            [`is_sandbox_mode`][csiapps.config.is_sandbox_mode].

    Returns:
        Callable: A zero-argument function returning the layout. Assign it
        straight to ``app.layout``; Dash calls it on **every** page load, which
        is what makes the signed-in name reflect the current user rather than
        whoever happened to load the app first. Because it is a callable and not
        a component, it cannot be nested inside another component — wrap the
        children instead.

    Example:
        ```python
        from dash import Dash, dcc
        from csiapps.dash import attach, layout_wrapper

        app = Dash(__name__)
        attach(app)
        app.layout = layout_wrapper(
            dcc.Dropdown(id="org"),
            nav_links=[{"label": "Reports", "href": "/reports"}],
        )
        ```

    Note:
        The chrome CSS is served by [`attach`][csiapps.dash.attach] at
        ``/csi-auth/chrome.css`` and linked from the layout, so ``attach`` must
        be called for the styling to load. No ``external_stylesheets``, no
        ``index_string`` override, and no ``assets`` folder to vendor.
    """

    def _layout():
        resolved = config.is_sandbox_mode() if sandbox is None else sandbox
        body = [
            html.Link(rel="stylesheet", href=CSS_ROUTE),
            _navbar(nav_links, resolved),
        ]
        if resolved:
            body.append(_sandbox_banner())
        body.append(
            # padding-bottom leaves room for the fixed-bottom footer so it never
            # overlaps app content on short pages (mirrors ui_wrapper).
            html.Div(
                [_auth_status(resolved), *children],
                style={"paddingBottom": "80px"},
            )
        )
        body.append(_footer())
        return html.Div(body)

    return _layout
