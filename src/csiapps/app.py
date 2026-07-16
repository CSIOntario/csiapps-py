"""Shiny for Python wrappers for CSIAPPS apps.

Port of ``R/shiny.R``: ``ui_wrapper`` (consistent navbar/footer chrome + auth
status + sandbox banner), ``server_wrapper`` (OAuth2 PKCE login, or a simulated
login in sandbox mode), and ``global_wrapper``.

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

import os
from urllib.parse import parse_qs, urlencode

import httpx
from shiny import reactive, render, ui

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


def global_wrapper(code):
    """Run app setup code so its definitions are visible to the server.

    In R this evaluated a code block in the global environment so ``server`` could
    see it. Python module scope already provides that, so this simply invokes a
    setup callable (if given) and returns its result.

    ponytail: near no-op in Python -- module-level ``x = ...`` covers the R use
    case. Kept for API parity.
    """
    return code() if callable(code) else code


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


def ui_wrapper(*args, sandbox=None):
    """Wrap app UI with the CSI navbar, footer, auth-status line, and (in sandbox
    mode) a banner making it obvious the app is not connected to the warehouse."""
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
    children.append(ui.output_ui("auth_status"))
    children.extend(args)
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


def server_wrapper(app_specific_logic, sandbox=None):
    """Wrap an app server function with CSIAPPS authentication handling."""
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
                    user_token.set(auth.exchange_code_for_token(code, code_verifier=verifier))

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
                    resp = httpx.get(
                        userinfo_url, headers={"Authorization": f"Bearer {access_token}"}
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
