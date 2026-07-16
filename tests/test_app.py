"""Ported from tests/testthat/test-sandbox-wrapper.R (ui_wrapper, server_wrapper,
global_wrapper). The deep reactive-session cases (simulated-login flushReact)
have no simple Shiny-for-Python equivalent, so the sandbox seeding + auth-status
logic is covered through the extracted pure helpers instead.
"""

from shiny import ui

from csiapps import app, global_wrapper, server_wrapper, set_institute, ui_wrapper

# ---- ui_wrapper --------------------------------------------------------


def test_ui_wrapper_shows_banner_only_in_sandbox():
    assert "Sandbox mode" in str(ui_wrapper(sandbox=True))
    assert "Sandbox mode" not in str(ui_wrapper(sandbox=False))


def test_ui_wrapper_injects_locked_chrome_styles():
    html = str(ui_wrapper(ui.div("app content"), sandbox=True))
    assert 'id="csi-navbar"' in html
    assert "#csi-navbar" in html
    assert "position: sticky" in html
    assert "background-color: #ffffff !important" in html
    assert "border-bottom: 3px solid #d81f26 !important" in html
    assert "app content" in html


def test_ui_wrapper_logo_follows_institute():
    set_institute("csiontario")
    assert "logo-csi-ontario" in str(ui_wrapper(sandbox=True))
    set_institute("csipacific")
    assert "csi-pacific-logo" in str(ui_wrapper(sandbox=True))


# ---- server_wrapper ----------------------------------------------------


def test_server_wrapper_returns_callable_in_both_modes():
    def logic(input, output, session):
        pass

    assert callable(server_wrapper(logic, sandbox=True))
    assert callable(server_wrapper(logic, sandbox=False))


# ---- sandbox seeding + auth-status text (extracted pure helpers) --------


def test_seed_token_value_without_token(monkeypatch):
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    seeded = app._seed_token_value()
    assert seeded.get("access_token") is None
    assert seeded["unauthenticated"] is True


def test_seed_token_value_with_token(monkeypatch):
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "dev-token-abc")
    seeded = app._seed_token_value()
    assert seeded["access_token"] == "dev-token-abc"
    assert "unauthenticated" not in seeded


def test_signed_in_text_variants():
    signed = app._signed_in_text({"first_name": "Ada", "last_name": "L"}, False)
    assert signed == "Signed in as Ada L"
    assert app._signed_in_text(None, True) == "Signed in (sandbox)"


# ---- global_wrapper ----------------------------------------------------


def test_global_wrapper_runs_callable_and_passes_through():
    assert global_wrapper(lambda: 42) == 42
    assert global_wrapper(7) == 7
