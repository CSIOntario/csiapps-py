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
* ``session$userData$csiapps_token`` -> :func:`csiapps.client.set_session_token`
  (keyed on the session), read back by :func:`csiapps.client.current_token`.
* ``session$sendCustomMessage`` is a coroutine here, so effects that send
  messages are ``async``.
"""

import asyncio
import os
from collections.abc import Callable
from urllib.parse import parse_qs, urlencode

import httpx
from shiny import reactive, render, ui
from shiny.types import TagChild
from shiny.ui import Tag

from . import auth, client, config

_FAVICON = "https://csiontario.ca/wp-content/uploads/2022/04/cropped-CSIO-Favicon-192x192.png"

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
    # Neutral frame: white bar, CSI-red accent line, soft shadow. Scoped by id +
    # !important so a wrapped app's theme/CSS cannot override it.
    accent = "#d81f26"
    bar_bg = "#ffffff"
    bar_text = "#1f2937"
    css = f"""
    #csi-navbar {{
      background-color: {bar_bg} !important;
      border-bottom: 3px solid {accent} !important;
      box-shadow: 0 2px 4px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
      position: sticky;
      top: 0;
      z-index: 1030;
    }}
    #csi-navbar .navbar-brand,
    #csi-navbar .navbar-brand:hover,
    #csi-navbar .navbar-nav .nav-link {{
      color: {bar_text} !important;
    }}
    #footer {{
      background-color: {bar_bg} !important;
      color: {bar_text} !important;
      border-top: 1px solid #e6e6e6 !important;
      z-index: 1030;
    }}
    #footer p, #footer a {{ color: {bar_text} !important; }}
    """
    return ui.tags.style(ui.HTML(css))


def _logo_src():
    if config.get_institute() == "csipacific":
        return "https://www.csipacific.ca/wp-content/uploads/2024/05/csi-pacific-logo-main.png"
    return "https://csiontario.ca/wp-content/uploads/2022/03/logo-csi-ontario.png"


def _navbar_ui():
    return ui.tags.nav(
        ui.tags.div(
            ui.tags.a(
                ui.tags.img(src=_logo_src(), height="48px", style="margin-right: 8px;"),
                class_="navbar-brand d-flex align-items-center",
                href="#",
            ),
            class_="container-fluid",
        ),
        id="csi-navbar",
        class_="navbar navbar-expand-lg navbar-light bg-white px-3",
    )


def _footer_ui():
    from datetime import date

    name = "CSI Pacific" if config.get_institute() == "csipacific" else "CSI Ontario"
    return ui.tags.footer(
        ui.tags.div(
            ui.tags.p(
                ui.HTML(f"&copy; {date.today().year} {name}"),
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
        ui.HTML("Sandbox mode &mdash; not connected to the live warehouse"),
        class_="text-center border-bottom",
        style="background:#faf6ec;color:#8a6d3b;font-size:12px;padding:3px 0;letter-spacing:.02em;",
    )


def ui_wrapper(*args: TagChild, sandbox: bool | None = None) -> Tag:
    """Wrap an app's UI in the standard CSI chrome.

    Adds the CSI navbar, footer, an auth-status line, the favicon and
    redirect/reset message handlers, and — in sandbox mode — a banner making it
    obvious the app is not connected to the live warehouse. Use it in place of
    ``ui.page_fluid`` at the top of an app's UI definition; pair it with
    [`server_wrapper`][csiapps.app.server_wrapper] on the server side.

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
        import csiapps

        app_ui = csiapps.ui_wrapper(
            ui.h2("My app"),
            ui.input_action_button("logout", "Log out"),
        )
        ```

    Note:
        The chrome styles are scoped by id and marked ``!important`` so a wrapped
        app's own theme cannot override the navbar and footer. Include an
        ``input_action_button("logout", ...)`` for the logout effect wired up by
        [`server_wrapper`][csiapps.app.server_wrapper].
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


def _seed_token_value():
    """The token value the sandbox seeds the session with (pure, so it is testable).

    Mirrors R's .sandbox_seed_session(): the developer's existing access token is
    adopted as the "granted" token; with none set, a sentinel marks the session
    unauthenticated (the shared consumer then short-circuits before any network
    call).
    """
    tok = os.environ.get("CSIAPPS_ACCESS_TOKEN", "")
    if tok:
        return {"access_token": tok, "sandbox": True}
    return {"sandbox": True, "unauthenticated": True}


def _signed_in_text(userinfo, sandbox):
    if userinfo and userinfo.get("first_name") and userinfo.get("last_name"):
        text = f"Signed in as {userinfo['first_name']} {userinfo['last_name']}"
    else:
        text = "Signed in"
    if sandbox:
        text += " (sandbox)"
    return text


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
        import csiapps

        def my_server(input, output, session):
            ...

        app = App(app_ui, csiapps.server_wrapper(my_server))
        ```

    Note:
        The wrapper registers a logout effect bound to an ``input.logout``
        action button — include one in the UI (see
        [`ui_wrapper`][csiapps.app.ui_wrapper]). Blocking token and ``/me`` calls
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
                    client.set_session_token(session, None)
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
            client.set_session_token(session, None)

            if tok is None or tok.get("error"):
                await session.send_custom_message("csip_reset", {})
                return

            access_token = tok.get("access_token")
            if not access_token:
                return

            client.set_session_token(session, access_token)

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
                return ui.tags.p(
                    ui.HTML(
                        "Not authenticated &mdash; set CSIAPPS_ACCESS_TOKEN "
                        "to emulate login in sandbox mode."
                    )
                )
            return ui.TagList(ui.tags.br(), ui.tags.p(_signed_in_text(userinfo(), sandbox)))

        @reactive.effect
        @reactive.event(input.logout, ignore_none=True)
        async def _logout():
            userinfo.set(None)
            client.set_session_token(session, None)
            if sandbox:
                user_token.set(_seed_token_value())
            else:
                user_token.set(None)
                await session.send_custom_message("csip_reset", {})

        # Call the app's own server function so it keeps its lexical scope.
        app_specific_logic(input, output, session)

    return server
