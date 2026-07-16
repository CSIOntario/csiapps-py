"""PKCE round-trip, token exchange, and check_secrets.

The R package had no dedicated auth unit tests (the flow was exercised through
Shiny); these lock the ported logic in place.
"""

import httpx
import pytest
import respx

from csiapps import auth, config


def test_pkce_state_roundtrip():
    pk = auth.generate_pkce()
    state = auth.pkce_state_encode(pk["verifier"])
    decoded = auth.pkce_state_decode(state)
    assert decoded["v"] == pk["verifier"]
    assert isinstance(decoded["r"], int)


def test_generate_pkce_is_url_safe_and_s256():
    pk = auth.generate_pkce()
    assert pk["method"] == "S256"
    for part in (pk["verifier"], pk["challenge"], auth.pkce_state_encode(pk["verifier"])):
        # base64url output: no '+', '/', or '=' padding
        assert not (set(part) & set("+/="))


def test_check_secrets_sandbox_never_raises():
    assert auth.check_secrets(sandbox=True) is True


def test_check_secrets_production_raises_on_bad_redirect():
    config.set_sandbox_mode(False)
    # no CSIAPPS_REDIRECT_URI set -> must flag it
    with pytest.raises(ValueError, match="CSIAPPS_REDIRECT_URI"):
        auth.check_secrets()


def test_check_secrets_production_ok(monkeypatch):
    config.set_sandbox_mode(False)
    monkeypatch.setenv("CSIAPPS_REDIRECT_URI", "https://apps.csiontario.ca/callback")
    assert auth.check_secrets() is True


@respx.mock
def test_exchange_code_success(monkeypatch):
    monkeypatch.setenv("CSIAPPS_CLIENT_ID", "cid")
    monkeypatch.setenv("CSIAPPS_CLIENT_SECRET", "secret")
    monkeypatch.setenv("CSIAPPS_REDIRECT_URI", "https://apps.csipacific.ca/callback")
    respx.post(config.token_url()).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "token_type": "Bearer"})
    )
    out = auth.exchange_code_for_token("thecode", code_verifier="v")
    assert out["access_token"] == "tok"


@respx.mock
def test_exchange_code_http_error_returns_dict():
    respx.post(config.token_url()).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    out = auth.exchange_code_for_token("badcode")
    assert out["error"] == "token_exchange_http_error"
    assert out["status"] == 400
    assert out["payload"] == {"error": "invalid_grant"}
