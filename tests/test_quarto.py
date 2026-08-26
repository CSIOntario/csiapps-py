"""Setup boundaries for csiapps.quarto."""

import asyncio

import pytest

pytest.importorskip("shiny", reason="csiapps[quarto] not installed")

from csiapps import quarto  # noqa: E402


def test_quarto_setup_requires_active_session(monkeypatch):
    monkeypatch.setattr(quarto, "_get_current_session", lambda: None)
    with pytest.raises(RuntimeError, match="active Shiny session"):
        quarto.quarto_setup()


def test_quarto_setup_is_a_noop_during_quarto_render(monkeypatch):
    monkeypatch.setattr(quarto, "_get_current_session", lambda: None)
    monkeypatch.setenv("QUARTO_DOCUMENT_PATH", ".")
    assert quarto.quarto_setup() is None


def test_quarto_setup_reveals_sandbox_without_a_token(monkeypatch, tmp_path):
    body = tmp_path / "body.html"
    body.write_text("<main>Local sandbox report</main>", encoding="utf-8")
    sent = []

    class Value:
        def __init__(self, value):
            self.value = value

        def __call__(self):
            return self.value

        def set(self, value):
            self.value = value

    class Session:
        async def send_custom_message(self, name, payload):
            sent.append((name, payload))

    session = Session()
    monkeypatch.setattr(quarto, "_get_current_session", lambda: session)
    monkeypatch.setattr(quarto.reactive, "value", Value)
    monkeypatch.setattr(quarto.reactive, "effect", lambda fn: (asyncio.run(fn()), fn)[1])

    def fake_wrapper(initialize, sandbox, pause_on_logout):
        assert pause_on_logout is True
        return lambda input, output, current: initialize(input, output, current)

    monkeypatch.setattr(
        quarto,
        "server_wrapper",
        fake_wrapper,
    )
    monkeypatch.setattr(
        quarto.client,
        "token_ready",
        lambda: pytest.fail("sandbox must not request a CSI token"),
    )

    quarto.quarto_setup(
        protected_body=body,
        config_file=tmp_path / "missing.json",
        sandbox=True,
    )

    assert sent == [
        (
            "csiapps_quarto_init",
            {"body": "<main>Local sandbox report</main>", "title": None},
        )
    ]
