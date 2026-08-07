"""csiapps.shiny: the ui_wrapper/server_wrapper chrome and the per-session token.

Ported from tests/testthat/test-sandbox-wrapper.R (ui_wrapper, server_wrapper).
The deep reactive-session cases (simulated-login flushReact) have no simple
Shiny-for-Python equivalent, so the sandbox seeding + auth-status logic is
covered through the extracted pure helpers instead. The per-session token store
and its adapter live here too (they moved out of the framework-neutral client),
including the pre-login quiet-gating that re-fires on login.

Skips entirely when the `[shiny]` extra is absent, mirroring test_dash.py.
"""

import asyncio

import httpx
import pytest
import respx

pytest.importorskip("shiny", reason="csiapps[shiny] not installed")

from shiny import reactive, ui  # noqa: E402

from csiapps import auth, chrome, client, config, set_institute  # noqa: E402
from csiapps import shiny as csishiny  # noqa: E402
from csiapps.shiny import server_wrapper, ui_wrapper  # noqa: E402

SITE = "https://apps.csipacific.ca"


class FakeSession:  # weak-referenceable stand-in for a Shiny session
    pass


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
    seeded = auth.seed_sandbox_token()
    assert seeded.get("access_token") is None
    assert seeded["unauthenticated"] is True


def test_seed_token_value_with_token(monkeypatch):
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "dev-token-abc")
    seeded = auth.seed_sandbox_token()
    assert seeded["access_token"] == "dev-token-abc"
    assert "unauthenticated" not in seeded


def test_signed_in_text_variants():
    signed = chrome.signed_in_text({"first_name": "Ada", "last_name": "L"}, False)
    assert signed == "Signed in as Ada L"
    assert chrome.signed_in_text(None, True) == "Signed in (sandbox)"


# ---- per-session token store + adapter ---------------------------------


def test_session_token_wins_over_env(monkeypatch):
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    sess = FakeSession()
    monkeypatch.setattr(csishiny, "_get_current_session", lambda: sess)
    csishiny.set_session_token(sess, "sesstok")
    assert client.current_token() == "sesstok"
    csishiny.set_session_token(sess, None)
    assert client.current_token() == "envtok"


def test_gates_quietly_and_refires_on_login(monkeypatch):
    # Inside a reactive context a not-yet-available token must not raise: the
    # adapter cancels quietly via req(), and the reactive re-runs on its own once
    # server_wrapper stores the token (the fix for the empty-dropdown race).
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)

    sess = FakeSession()
    monkeypatch.setattr(csishiny, "_get_current_session", lambda: sess)
    csishiny.set_session_token(sess, None)

    state = {"runs": 0, "reached_api": 0}

    @reactive.effect
    def _consumer():
        state["runs"] += 1
        # A gated helper called before login: raises no error, cancels quietly.
        client.fetch_org_options(sandbox=False)
        state["reached_api"] += 1

    async def drive():
        await reactive.flush()
        assert state["runs"] == 1
        assert state["reached_api"] == 0  # gated pre-login, no error surfaced

        csishiny.set_session_token(sess, "tok-abc")
        await reactive.flush()
        assert state["runs"] == 2  # re-fired on its own once the token arrived
        assert state["reached_api"] == 1  # got past the gate and completed the call

    with respx.mock:
        respx.get(f"{SITE}{config.SPORT_ORG_ENDPOINT}").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        asyncio.run(drive())

    _consumer.destroy()
