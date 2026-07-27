"""Shiny for Python wrappers for CSIAPPS apps.

Port of ``R/shiny.R``: ``ui_wrapper`` (consistent navbar/footer chrome + auth
status + sandbox banner) and ``server_wrapper`` (OAuth2 PKCE login, or a
simulated login in sandbox mode).

Framework mapping notes (R Shiny -> Shiny for Python):

* ``reactiveVal`` -> ``reactive.value``; ``observe`` -> ``@reactive.effect``;
  ``observeEvent`` -> ``@reactive.effect`` + ``@reactive.event``;
  ``renderUI``/``uiOutput`` -> ``@render.ui``/``ui.output_ui``.
* ``shinyjs::runjs`` has no Python port; the two `window.location` nudges use a
  custom-message handler (``csip_reset``) injected in the head, same mechanism as
  the redirect (``csip_redirect``). ``useShinyjs()`` is dropped.
* ``session$userData$csiapps_token`` -> :func:`csiapps.shiny.set_session_token`
  (keyed on the session), read back by :func:`csiapps.client.current_token`.
* ``session$sendCustomMessage`` is a coroutine here, so effects that send
  messages are ``async``.
"""

import asyncio
import os
import weakref
from collections.abc import Callable
from urllib.parse import parse_qs, urlencode

import httpx
from shiny import reactive, render, req, ui
from shiny.types import TagChild
from shiny.ui import Tag

from . import auth, chrome, client, config

# The chrome constants and copy live in csiapps.chrome (framework-independent)
# so csiapps.dash renders the identical navbar/footer without importing Shiny.
# Re-bound here under their original private names: nothing outside this module
# should have to care where they moved.
_FAVICON = chrome.FAVICON
_seed_token_value = auth.seed_sandbox_token
_signed_in_text = chrome.signed_in_text


# ---- per-session token store + adapter ---------------------------------
#
# The access token is stored per Shiny session (keyed on the session object, the
# faithful analog of R storing it on session$userData) so concurrent users never
# share a token. server_wrapper writes it here at login; the adapter below is
# registered with the framework-neutral client so client.current_token() reads
# it back without importing Shiny.
#
# The value is a reactive.value (not a plain string) so that reading it inside a
# reactive context registers a dependency: an app reactive that calls a fetch_*
# helper before login completes is cancelled quietly (the adapter's
# handle_missing_token calls req(False)) and re-runs on its own once the token
# lands. Lazily created so it does not matter whether the wrapper or the app
# touches the session first.
_session_tokens: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _get_current_session():
    # Indirection so tests can stub the active session without a running app.
    try:
        from shiny.session import get_current_session

        return get_current_session()
    except Exception:
        return None


def _in_reactive_context() -> bool:
    # True inside a reactive effect/calc/render, where req() has a computation to
    # cancel and re-run. Outside one (CLI, scripts, plain tests) req() would just
    # raise, so callers there should fail loudly instead.
    try:
        from shiny.reactive._core import get_current_context

        get_current_context()
        return True
    except Exception:
        return False


def _token_rv(session) -> "reactive.Value":
    rv = _session_tokens.get(session)
    if rv is None:
        rv = reactive.value(None)
        _session_tokens[session] = rv
    return rv


def set_session_token(session, token) -> None:
    """Store (or clear, when ``token`` is falsy) the access token for a session."""
    _token_rv(session).set(token or None)


class _ShinyTokenAdapter:
    """Reads the per-session Shiny token for :func:`csiapps.client.current_token`."""

    def read_token(self):
        session = _get_current_session()
        if session is None:
            return None
        rv = _token_rv(session)
        if _in_reactive_context():
            tok = rv()  # establishes a dependency -> re-run on login
        else:
            with reactive.isolate():
                tok = rv()
        return tok or None

    def handle_missing_token(self) -> bool:
        # Inside a reactive context a missing token is the normal pre-login
        # state, so cancel the computation with req(False); because read_token
        # took a dependency on the token's reactive value, it re-runs once login
        # completes. Outside a reactive context let the core raise loudly.
        if _in_reactive_context():
            req(False)
        return False


client.register_token_adapter(_ShinyTokenAdapter())

_HANDLERS_JS = """
Shiny.addCustomMessageHandler('csip_redirect', function(url) {
  if (url && typeof url === 'string') { window.top.location.href = url; }
});
Shiny.addCustomMessageHandler('csip_reset', function(x) {
  window.location.href = window.location.pathname;
});
"""


# ---- chrome (navbar / footer / styles) ---------------------------------


def _csi_chrome_styles():
    return ui.tags.style(ui.HTML(chrome.CHROME_CSS))


def _logo_src():
    return chrome.logo_src()


def _navbar_ui():
    return ui.tags.nav(
        ui.tags.div(
            ui.tags.a(
                ui.tags.img(
                    src=chrome.logo_src(),
                    height=chrome.LOGO_HEIGHT,
                    style="margin-right: 8px;",
                ),
                class_="navbar-brand d-flex align-items-center",
                href="#",
            ),
            class_="container-fluid",
        ),
        id="csi-navbar",
        class_="navbar navbar-expand-lg navbar-light bg-white px-3",
    )


def _footer_ui():
    return ui.tags.footer(
        ui.tags.div(
            ui.tags.p(
                ui.HTML(chrome.footer_text()),
                class_="col-md-4 mb-0",
            ),
            ui.tags.ul(class_="nav col-md-4 justify-content-end"),
            class_="d-flex flex-wrap justify-content-between align-items-center py-3 container",
        ),
        id="footer",
        class_="mt-4 bg-dark text-white border-top border-light fixed-bottom",
    )


def _sandbox_banner():
    return ui.tags.div(
        ui.HTML(chrome.SANDBOX_BANNER_TEXT),
        class_=chrome.SANDBOX_BANNER_CLASS,
        style=chrome.SANDBOX_BANNER_STYLE,
    )


def ui_wrapper(*args: TagChild, sandbox: bool | None = None) -> Tag:
    """Wrap an app's UI in the standard CSI chrome.

    Adds the CSI navbar, footer, an auth-status line, the favicon and
    redirect/reset message handlers, and — in sandbox mode — a banner making it
    obvious the app is not connected to the live warehouse. Use it in place of
    ``ui.page_fluid`` at the top of an app's UI definition; pair it with
    [`server_wrapper`][csiapps.shiny.server_wrapper] on the server side.

    Args:
        *args: The app's own UI elements (Shiny tags / components), rendered
            below the auth-status line inside the chrome.
        sandbox: Force the sandbox banner on (``True``) or off (``False``).
            ``None`` (the default) resolves via
            [`is_sandbox_mode`][csiapps.config.is_sandbox_mode].

    Returns:
        A ``ui.page_fluid`` page containing the chrome and the supplied UI.

    Example:
        ```python
        from shiny import ui
        from csiapps.shiny import ui_wrapper

        app_ui = ui_wrapper(
            ui.h2("My app"),
            ui.input_action_button("logout", "Log out"),
        )
        ```

    Note:
        The chrome styles are scoped by id and marked ``!important`` so a wrapped
        app's own theme cannot override the navbar and footer. Include an
        ``input_action_button("logout", ...)`` for the logout effect wired up by
        [`server_wrapper`][csiapps.shiny.server_wrapper].
    """
    if sandbox is None:
        sandbox = config.is_sandbox_mode()

    children = [
        ui.head_content(
            ui.tags.script(ui.HTML(_HANDLERS_JS)),
            ui.tags.link(rel="shortcut icon", href=_FAVICON),
        ),
        _csi_chrome_styles(),
        _navbar_ui(),
    ]
    if sandbox:
        children.append(_sandbox_banner())
    # padding-bottom leaves room for the fixed-bottom footer so it never overlaps
    # app content on short pages (mirrors the R wrapper's fluidPage style).
    children.append(
        ui.div(
            ui.output_ui("auth_status"),
            *args,
            style="padding-bottom: 80px;",
        )
    )
    children.append(_footer_ui())
    return ui.page_fluid(*children)


# ---- server ------------------------------------------------------------


def server_wrapper(
    app_specific_logic: Callable, sandbox: bool | None = None
) -> Callable:
    """Wrap an app's server function with CSIAPPS authentication.

    Returns a Shiny server function that handles login before delegating to your
    own server logic. In production it runs the OAuth2 PKCE flow (redirect to
    CSIAPPS, exchange the returned code for a token, load ``/me`` for the header).
    In sandbox mode it simulates that login using ``CSIAPPS_ACCESS_TOKEN`` if
    set, or marks the session unauthenticated otherwise. Either way the
    per-session token is stored so [`make_request`][csiapps.client.make_request]
    and the ``fetch_*`` helpers pick it up automatically.

    Args:
        app_specific_logic: Your app's server function with the usual Shiny
            ``(input, output, session)`` signature. It is called after auth is
            wired up, keeping its own lexical scope.
        sandbox: Force sandbox (``True``) or production (``False``) auth
            behaviour. ``None`` (the default) resolves via
            [`is_sandbox_mode`][csiapps.config.is_sandbox_mode].

    Returns:
        Callable: A server function to hand to Shiny's ``App(app_ui, server)``.

    Example:
        ```python
        from shiny import App
        from csiapps.shiny import server_wrapper

        def my_server(input, output, session):
            ...

        app = App(app_ui, server_wrapper(my_server))
        ```

    Note:
        The wrapper registers a logout effect bound to an ``input.logout``
        action button — include one in the UI (see
        [`ui_wrapper`][csiapps.shiny.ui_wrapper]). Blocking token and ``/me`` calls
        run off the event loop so a slow endpoint cannot stall other sessions.
    """
    if sandbox is None:
        sandbox = config.is_sandbox_mode()

    def server(input, output, session):
        user_token = reactive.value(None)
        userinfo = reactive.value(None)

        if sandbox:
            # Simulate the redirect: seed the token from the environment and hand
            # it to the same consumer a production login would.
            user_token.set(_seed_token_value())
        else:

            @reactive.effect
            async def _oauth():
                qs = parse_qs(session.clientdata.url_search().lstrip("?"))
                code = qs.get("code", [None])[0]
                state = qs.get("state", [None])[0]
                err = qs.get("error", [None])[0]

                if err:
                    user_token.set(
                        {"error": err, "error_description": qs.get("error_description", [None])[0]}
                    )
                    set_session_token(session, None)
                    await session.send_custom_message("csip_reset", {})

                # 1) no code + no token -> redirect to CSI
                if code is None and user_token() is None:
                    pk = auth.generate_pkce()
                    st = auth.pkce_state_encode(pk["verifier"])
                    params = {
                        "response_type": "code",
                        "client_id": os.environ.get("CSIAPPS_CLIENT_ID", ""),
                        "redirect_uri": os.environ.get("CSIAPPS_REDIRECT_URI", ""),
                        "scope": os.environ.get("CSIAPPS_SCOPE", "read write"),
                        "code_challenge": pk["challenge"],
                        "code_challenge_method": pk["method"],
                        "state": st,
                    }
                    await session.send_custom_message(
                        "csip_redirect", config.auth_url() + "?" + urlencode(params)
                    )
                    return

                # 2) have code but no token yet -> exchange
                if code is not None and user_token() is None:
                    verifier = auth.pkce_state_decode(state).get("v") if state else None
                    # exchange_code_for_token does a *blocking* httpx.post; run it
                    # off the event loop so a slow token endpoint can't stall every
                    # other session (a Python-only concern -- R has no shared loop).
                    tok = await asyncio.to_thread(
                        auth.exchange_code_for_token, code, verifier
                    )
                    user_token.set(tok)

        # Shared consumer (production + sandbox): store the token per-session and
        # load /me for the header.
        @reactive.effect
        @reactive.event(user_token)
        async def _consume():
            tok = user_token()
            set_session_token(session, None)

            if tok is None or tok.get("error"):
                await session.send_custom_message("csip_reset", {})
                return

            access_token = tok.get("access_token")
            if not access_token:
                return

            set_session_token(session, access_token)

            userinfo_url = config.userinfo_url()
            if userinfo_url:
                try:
                    # Blocking httpx.get -> run off the event loop so a slow /me
                    # endpoint can't stall other sessions (Python-only concern).
                    resp = await asyncio.to_thread(
                        httpx.get,
                        userinfo_url,
                        headers={"Authorization": f"Bearer {access_token}"},
                        follow_redirects=True,
                    )
                    resp.raise_for_status()
                    userinfo.set(resp.json())
                except Exception as e:  # a stale/expired token degrades gracefully
                    ui.notification_show(f"Error loading user info: {e}", type="error")

        @render.ui
        def auth_status():
            tok = user_token()
            if tok is None:
                return ui.tags.p("Redirecting to CSIAPPS for authentication...")
            if tok.get("error"):
                return ui.tags.p("Authentication error (see logs).")
            if tok.get("unauthenticated"):
                return ui.tags.p(ui.HTML(chrome.UNAUTHENTICATED_TEXT))
            return ui.TagList(ui.tags.br(), ui.tags.p(_signed_in_text(userinfo(), sandbox)))

        @reactive.effect
        @reactive.event(input.logout, ignore_none=True)
        async def _logout():
            userinfo.set(None)
            set_session_token(session, None)
            if sandbox:
                user_token.set(_seed_token_value())
            else:
                user_token.set(None)
                await session.send_custom_message("csip_reset", {})

        # Call the app's own server function so it keeps its lexical scope.
        app_specific_logic(input, output, session)

    return server
