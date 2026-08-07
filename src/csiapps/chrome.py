"""Framework-independent CSI chrome: styles, logos, and the copy around them.

Both :mod:`csiapps.shiny` (Shiny) and :mod:`csiapps.dash` (Dash) render the same
navbar, footer, sandbox banner and auth-status line. Only the *tag construction*
differs between the two frameworks; the CSS, the logo URLs and the wording do
not. They live here so the two renderers cannot drift, and so that a Dash
process never has to import Shiny to reach them.

Nothing in this module imports a web framework.

ponytail: module constants and three one-line functions, not a Theme class.
There is one chrome, parameterised by one institute.
"""

from datetime import date

from . import config

FAVICON = "https://csiontario.ca/wp-content/uploads/2022/04/cropped-CSIO-Favicon-192x192.png"

# Neutral frame: white bar, CSI-red accent line, soft shadow. Scoped by id +
# !important so a wrapped app's theme/CSS cannot override it.
CHROME_CSS = """
    #csi-navbar {
      background-color: #ffffff !important;
      border-bottom: 3px solid #d81f26 !important;
      box-shadow: 0 2px 4px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
      position: sticky;
      top: 0;
      z-index: 1030;
    }
    #csi-navbar .navbar-brand,
    #csi-navbar .navbar-brand:hover,
    #csi-navbar .navbar-nav .nav-link {
      color: #1f2937 !important;
    }
    /* Pin the logo size. The height is set as an HTML attribute (low priority),
       so a wrapped app's own `img {}` rule (e.g. `height:auto`/`max-width:100%`
       from a theme or Bootstrap) would otherwise resize the logo and push it
       through the red accent line. */
    #csi-navbar .navbar-brand img {
      height: 48px !important;
      width: auto !important;
      max-width: none !important;
    }
    #footer {
      background-color: #ffffff !important;
      color: #1f2937 !important;
      border-top: 1px solid #e6e6e6 !important;
      z-index: 1030;
    }
    #footer p, #footer a { color: #1f2937 !important; }
    """

# Copy. Em dashes are literal characters, not `&mdash;`: Shiny passes these
# through `ui.HTML` and Dash hands them to React as text, and only a literal
# renders correctly in both.
SANDBOX_BANNER_TEXT = "Sandbox mode — not connected to the live warehouse"
SANDBOX_BANNER_CLASS = "text-center border-bottom"
SANDBOX_BANNER_STYLE = (
    "background:#faf6ec;color:#8a6d3b;font-size:12px;padding:3px 0;letter-spacing:.02em;"
)

UNAUTHENTICATED_TEXT = (
    "Not authenticated — set CSIAPPS_ACCESS_TOKEN to emulate login in sandbox mode."
)

_LOGOS = {
    "csipacific": "https://www.csipacific.ca/wp-content/uploads/2024/05/csi-pacific-logo-main.png",
    "csiontario": "https://csiontario.ca/wp-content/uploads/2022/03/logo-csi-ontario.png",
}
_DISPLAY_NAMES = {"csipacific": "CSI Pacific", "csiontario": "CSI Ontario"}

LOGO_HEIGHT = "48px"


def logo_src() -> str:
    """The institute's logo URL, as a public https URL (never a vendored file)."""
    return _LOGOS.get(config.get_institute(), _LOGOS["csiontario"])


def institute_name() -> str:
    """The institute's display name, as it appears in the footer."""
    return _DISPLAY_NAMES.get(config.get_institute(), _DISPLAY_NAMES["csiontario"])


def footer_text() -> str:
    """The footer copyright line, e.g. ``'© 2026 CSI Pacific'``."""
    return f"© {date.today().year} {institute_name()}"


def signed_in_text(userinfo: dict | None, sandbox: bool) -> str:
    """The auth-status line for a signed-in user.

    Falls back to a bare "Signed in" when ``/me`` gave no name (or has not
    loaded yet), and marks sandbox sessions so a simulated login is never
    mistaken for a real one.
    """
    if userinfo and userinfo.get("first_name") and userinfo.get("last_name"):
        text = f"Signed in as {userinfo['first_name']} {userinfo['last_name']}"
    else:
        text = "Signed in"
    if sandbox:
        text += " (sandbox)"
    return text
