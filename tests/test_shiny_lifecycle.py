"""Exercise the actual wrapper effects with Shiny's reactive scheduler."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
import respx

pytest.importorskip("shiny", reason="csiapps[shiny/quarto] not installed")
from shiny import reactive  # noqa: E402

from csiapps import auth, client, config, quarto  # noqa: E402
from csiapps import shiny as wrapper  # noqa: E402


@pytest.fixture
def runtime(monkeypatch):
    effects, outputs, sent = [], {}, []
    effect = reactive.effect
    def track(fn):
        result = effect(fn, session=None)
        effects.append(result)
        return result
    def render(fn):
        outputs[fn.__name__] = fn
        return fn
    class Session:
        async def send_custom_message(self, name, payload):
            sent.append((name, payload))
    session = Session()
    search = reactive.value("?code=code&state=" + auth.pkce_state_encode("verifier"))
    session.clientdata = SimpleNamespace(url_search=search)
    inputs = SimpleNamespace(logout=reactive.value(None), csiapps_sign_in=reactive.value(None))
    monkeypatch.setattr(wrapper, "_get_current_session", lambda: session)
    monkeypatch.setattr(quarto, "_get_current_session", lambda: session)
    monkeypatch.setattr(reactive, "effect", track)
    monkeypatch.setattr(wrapper.render, "ui", render)
    monkeypatch.setattr(wrapper.ui, "notification_show", lambda *args, **kwargs: None)
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "console-token")
    monkeypatch.setattr(auth, "exchange_code_for_token", lambda *args: {"access_token": "user-a"})
    with respx.mock as http:
        http.get(config.userinfo_url()).respond(
            200, json={"first_name": "Ada", "last_name": "Lovelace"})
        yield SimpleNamespace(session=session, inputs=inputs, outputs=outputs,
                              sent=sent, search=search, http=http)
    for instance in effects:
        instance.destroy()


def status(runtime):
    with reactive.isolate():
        return str(runtime.outputs["auth_status"]())


def test_login_expiry_and_manual_sign_in(runtime):
    wrapper.server_wrapper(lambda *args: None, sandbox=False)(
        runtime.inputs, None, runtime.session)
    async def drive():
        await reactive.flush()
        assert client.current_token() == "user-a"
        assert "Ada" in status(runtime)
        assert ("csiapps_clean_query", {}) in runtime.sent
        assert not any(name == "csip_reset" for name, _ in runtime.sent)
        runtime.http.get("https://apps.csipacific.ca/api/test/").respond(401)
        with pytest.raises(client.AuthenticationError):
            client.make_request("api/test/", sandbox=False)
        await reactive.flush()
        assert not client.token_ready()
        assert "expired" in status(runtime)
        assert "Sign in" in status(runtime)
        assert ("csiapps_quarto_signed_out", {}) in runtime.sent
        assert ("csiapps_auth", {"authenticated": False, "expired": True}) in runtime.sent
        runtime.inputs.csiapps_sign_in.set(1)
        await reactive.flush()
        assert runtime.sent[-1] == ("csip_reset", {})
    asyncio.run(drive())


@pytest.mark.parametrize("failure", ["transport", "malformed", "denied", "state"])
def test_failed_login_is_stable_and_retryable(runtime, monkeypatch, failure):
    def exchange(*args):
        if failure == "transport":
            raise httpx.ConnectError("offline")
        return []
    monkeypatch.setattr(auth, "exchange_code_for_token", exchange)
    if failure == "denied":
        runtime.search.set("?error=access_denied")
    elif failure == "state":
        runtime.search.set("?code=code&state=invalid!")
    wrapper.server_wrapper(lambda *args: None, sandbox=False)(
        runtime.inputs, None, runtime.session)
    async def drive():
        await reactive.flush()
        assert not client.token_ready()
        assert "Authentication failed" in status(runtime)
        assert "Sign in" in status(runtime)
        assert not any(name in {"csip_reset", "csip_redirect"} for name, _ in runtime.sent)
        runtime.inputs.csiapps_sign_in.set(1)
        await reactive.flush()
        assert runtime.sent[-1][0] == "csip_reset"
    asyncio.run(drive())


def test_paused_logout_does_not_reauthenticate(runtime):
    wrapper.server_wrapper(lambda *args: None, sandbox=False, pause_on_logout=True)(
        runtime.inputs, None, runtime.session)
    async def drive():
        await reactive.flush()
        runtime.sent.clear()
        runtime.inputs.logout.set(1)
        await reactive.flush()
        assert not client.token_ready()
        assert "Signed out" in status(runtime)
        assert "Sign in" in status(runtime)
        assert ("csiapps_quarto_signed_out", {}) in runtime.sent
        assert not any(name in {"csip_reset", "csip_redirect"} for name, _ in runtime.sent)
    asyncio.run(drive())


def test_two_viewers_reject_only_matching_token(runtime, monkeypatch):
    class Session:
        pass
    other = Session()
    wrapper.set_session_token(runtime.session, "user-a")
    wrapper.set_session_token(other, "user-b")
    runtime.http.get("https://apps.csipacific.ca/api/test/").respond(401)
    with pytest.raises(client.AuthenticationError):
        client.make_request("api/test/", sandbox=False)
    assert not client.token_ready()
    monkeypatch.setattr(wrapper, "_get_current_session", lambda: other)
    assert client.current_token() == "user-b"


def test_userinfo_401_invalidates_login(runtime):
    runtime.http.get(config.userinfo_url()).respond(401)
    wrapper.server_wrapper(lambda *args: None, sandbox=False)(
        runtime.inputs, None, runtime.session)
    async def drive():
        await reactive.flush()
        assert not client.token_ready()
        assert "expired" in status(runtime)
    asyncio.run(drive())


def test_quarto_removes_body_on_expiry_and_paused_logout(runtime, tmp_path, monkeypatch):
    import shiny.express
    monkeypatch.setattr(shiny.express, "input", runtime.inputs)
    body = tmp_path / "body.html"
    body.write_text("<main>Protected report</main>")
    quarto.quarto_setup(body, tmp_path / "missing.json", sandbox=False)
    async def drive():
        await reactive.flush()
        assert any(name == "csiapps_quarto_init" and payload["body"] == body.read_text()
                   for name, payload in runtime.sent)
        wrapper.set_session_token(runtime.session, None)
        await reactive.flush()
        assert ("csiapps_quarto_signed_out", {}) in runtime.sent
        assert not client.token_ready()
        runtime.sent.clear()
        await reactive.flush()
        assert not any(name == "csiapps_quarto_init" for name, _ in runtime.sent)
    asyncio.run(drive())
