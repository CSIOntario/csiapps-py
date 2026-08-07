"""csiapps.chrome, and the parity it exists to guarantee.

The Shiny and Dash wrappers build different tag trees but must produce the same
CSI chrome. Rather than duplicating the CSS in both renderers and pinning the
copies with an equality assertion, the constants live in one
framework-independent module and these tests assert both renderers actually emit
them — which is the property that was really wanted.
"""

from datetime import date

import pytest

from csiapps import chrome, set_institute

# ---- the module itself -------------------------------------------------


def test_logo_follows_the_institute():
    set_institute("csipacific")
    assert "csi-pacific-logo" in chrome.logo_src()
    set_institute("csiontario")
    assert "logo-csi-ontario" in chrome.logo_src()
    set_institute("csiatlantic")
    assert "logo-institute.png" in chrome.logo_src()


def test_logo_is_always_a_public_https_url():
    # Apps stop vendoring csi-pacific-logo-reverse.png and stop patching
    # server.static_folder; that only works if this is never a local path.
    for institute in ("csipacific", "csiontario", "csiatlantic"):
        set_institute(institute)
        assert chrome.logo_src().startswith("https://")


def test_institute_name_and_footer():
    set_institute("csipacific")
    assert chrome.institute_name() == "CSI Pacific"
    assert chrome.footer_text() == f"© {date.today().year} CSI Pacific"
    set_institute("csiontario")
    assert chrome.institute_name() == "CSI Ontario"
    set_institute("csiatlantic")
    assert chrome.institute_name() == "CSI Atlantic"
    assert chrome.footer_text() == f"© {date.today().year} CSI Atlantic"


@pytest.mark.parametrize(
    "userinfo,sandbox,expected",
    [
        ({"first_name": "Ada", "last_name": "L"}, False, "Signed in as Ada L"),
        ({"first_name": "Ada", "last_name": "L"}, True, "Signed in as Ada L (sandbox)"),
        (None, False, "Signed in"),
        (None, True, "Signed in (sandbox)"),
        ({}, False, "Signed in"),
        ({"first_name": "Ada"}, False, "Signed in"),          # half a name is no name
        ({"first_name": "", "last_name": "L"}, False, "Signed in"),
    ],
)
def test_signed_in_text_variants(userinfo, sandbox, expected):
    assert chrome.signed_in_text(userinfo, sandbox) == expected


def test_copy_uses_literal_em_dashes_not_entities():
    # Shiny passes these through ui.HTML and Dash hands them to React as text.
    # An &mdash; entity renders literally in Dash, so only the character works
    # in both.
    for text in (chrome.SANDBOX_BANNER_TEXT, chrome.UNAUTHENTICATED_TEXT):
        assert "&mdash;" not in text
        assert "—" in text


def test_chrome_module_imports_no_web_framework():
    # The whole point of the module: a Dash process must reach the CSS without
    # importing Shiny, and vice versa.
    import ast
    import pathlib

    source = pathlib.Path(chrome.__file__).read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"shiny", "dash", "flask", "dash_auth"}


# ---- cross-framework parity --------------------------------------------

# Parity needs both frameworks rendering at once, so skip unless both extras are
# present. Skip on the third-party modules, not on csiapps.shiny/csiapps.dash:
# csiapps.dash raises a guided ImportError whose name is "dash", and pytest 8's
# importorskip re-raises anything that is not a ModuleNotFoundError for the
# module it was asked about.
pytest.importorskip("shiny", reason="csiapps[shiny] not installed")
pytest.importorskip("dash", reason="csiapps[dash] not installed")
pytest.importorskip("dash_auth", reason="csiapps[dash] not installed")

from csiapps import dash as csidash  # noqa: E402
from csiapps.shiny import ui_wrapper  # noqa: E402


def render_dash(**kwargs):
    return str(csidash.layout_wrapper(**kwargs)())


def test_both_frameworks_use_the_same_logo():
    for institute in ("csipacific", "csiontario", "csiatlantic"):
        set_institute(institute)
        assert chrome.logo_src() in str(ui_wrapper(sandbox=True))
        assert chrome.logo_src() in render_dash(sandbox=True)


def test_both_frameworks_use_the_same_navbar_and_footer_ids():
    for html in (str(ui_wrapper(sandbox=True)), render_dash(sandbox=True)):
        assert "csi-navbar" in html
        assert "footer" in html


def test_both_frameworks_show_the_same_banner_text():
    assert chrome.SANDBOX_BANNER_TEXT in str(ui_wrapper(sandbox=True))
    assert chrome.SANDBOX_BANNER_TEXT in render_dash(sandbox=True)
    assert chrome.SANDBOX_BANNER_TEXT not in str(ui_wrapper(sandbox=False))
    assert chrome.SANDBOX_BANNER_TEXT not in render_dash(sandbox=False)


def test_both_frameworks_show_the_same_footer_text():
    set_institute("csiontario")
    assert chrome.footer_text() in str(ui_wrapper(sandbox=True))
    assert chrome.footer_text() in render_dash(sandbox=True)


def test_both_frameworks_show_the_same_unauthenticated_copy(monkeypatch):
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    # Shiny renders this from the reactive auth_status; the string is what is
    # pinned here, since that is what can drift.
    import csiapps.shiny as shiny_app

    assert chrome.UNAUTHENTICATED_TEXT in str(shiny_app.chrome.UNAUTHENTICATED_TEXT)
    assert chrome.UNAUTHENTICATED_TEXT in render_dash(sandbox=True)


def test_shiny_still_emits_the_locked_chrome_css():
    # The assertions the pre-existing suite makes, restated against the shared
    # constant: the refactor must not have changed a byte of what Shiny renders.
    html = str(ui_wrapper(sandbox=True))
    for rule in (
        "background-color: #ffffff !important",
        "border-bottom: 3px solid #d81f26 !important",
        "position: sticky",
        "height: 48px !important",
        "max-width: none !important",
    ):
        assert rule in html
        assert rule in chrome.CHROME_CSS
