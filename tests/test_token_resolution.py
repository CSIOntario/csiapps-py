"""The framework-neutral token seam: client's adapter registry.

``client.current_token()`` resolves through registered framework adapters and
then the ``CSIAPPS_ACCESS_TOKEN`` env var; ``_auth_gate`` lets an adapter handle
a missing token (Shiny's quiet reactive-gating) or else raises loudly (Dash, and
CLI/scripts). This is the one seam csiapps.shiny / csiapps.dash reach into, so it
is pinned here with fake adapters and NO framework import -- it must hold on a
core-only install. Each real adapter's own behaviour lives in test_shiny.py and
test_dash.py.
"""

import pytest

from csiapps import client
from csiapps.client import current_token, make_request, token_ready


@pytest.fixture
def clean_registry(monkeypatch):
    # Isolate from any adapter other test modules registered at import time.
    monkeypatch.setattr(client, "_token_adapters", [])
    return client._token_adapters


class FakeAdapter:
    def __init__(self, token=None, handles=False):
        self._token = token
        self._handles = handles

    def read_token(self):
        return self._token

    def handle_missing_token(self):
        return self._handles


# ---- resolution order --------------------------------------------------


def test_env_fallback_when_no_adapter(clean_registry, monkeypatch):
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    assert current_token() == "envtok"


def test_adapter_token_wins_over_env(clean_registry, monkeypatch):
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    client.register_token_adapter(FakeAdapter(token="adaptertok"))
    assert current_token() == "adaptertok"


def test_first_adapter_with_a_token_wins(clean_registry):
    client.register_token_adapter(FakeAdapter(token=None))
    client.register_token_adapter(FakeAdapter(token="second"))
    assert current_token() == "second"


def test_empty_adapter_token_falls_through_to_env(clean_registry, monkeypatch):
    # An empty string is not a token; it must not mask the env var.
    monkeypatch.setenv("CSIAPPS_ACCESS_TOKEN", "envtok")
    client.register_token_adapter(FakeAdapter(token=""))
    assert current_token() == "envtok"


def test_no_token_anywhere_is_empty_string(clean_registry, monkeypatch):
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    assert current_token() == ""
    assert token_ready() is False


def test_register_token_adapter_is_idempotent(clean_registry):
    a = FakeAdapter(token="x")
    client.register_token_adapter(a)
    client.register_token_adapter(a)
    assert client._token_adapters.count(a) == 1


# ---- the auth gate -----------------------------------------------------


def test_missing_token_raises_when_no_adapter_handles(clean_registry, monkeypatch):
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    client.register_token_adapter(FakeAdapter(token=None, handles=False))
    with pytest.raises(RuntimeError, match="no CSIAPPS_ACCESS_TOKEN set"):
        make_request("api/csiauth/me/", sandbox=False)


def test_auth_gate_is_silenced_when_an_adapter_handles(clean_registry, monkeypatch):
    # An adapter that handles the missing token suppresses the raise -- the
    # framework-neutral form of Shiny's quiet reactive-gating.
    monkeypatch.delenv("CSIAPPS_ACCESS_TOKEN", raising=False)
    client.register_token_adapter(FakeAdapter(token=None, handles=True))
    assert client._auth_gate("test") is None  # must not raise
