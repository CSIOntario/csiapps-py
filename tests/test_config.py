"""Ported from tests/testthat/test-sandbox-mode.R (is_sandbox_mode portion) plus
set_institute / URL helpers. make_request routing tests land in phases 3-4.
"""

import pytest

from csiapps import config
from csiapps.config import is_sandbox_mode, set_institute, set_sandbox_mode


def test_sandbox_mode_true_by_default():
    assert is_sandbox_mode() is True


def test_env_disables_sandbox_only_for_literal_production(monkeypatch):
    monkeypatch.setenv("CSIAPPS_ENV", "production")
    assert is_sandbox_mode() is False

    # any other value leaves sandbox on (fail-safe: never route to prod by typo)
    monkeypatch.setenv("CSIAPPS_ENV", "staging")
    assert is_sandbox_mode() is True


def test_override_respected():
    set_sandbox_mode(True)
    assert is_sandbox_mode() is True
    set_sandbox_mode(False)
    assert is_sandbox_mode() is False


def test_env_var_respected(monkeypatch):
    monkeypatch.setenv("CSIAPPS_ENV", "sandbox")
    assert is_sandbox_mode() is True
    monkeypatch.setenv("CSIAPPS_ENV", "production")
    assert is_sandbox_mode() is False


def test_override_takes_precedence_over_env(monkeypatch):
    monkeypatch.setenv("CSIAPPS_ENV", "sandbox")
    set_sandbox_mode(False)
    assert is_sandbox_mode() is False


def test_non_bool_override_is_false():
    # mirrors R: a non-logical csiapps.sandbox option is treated as FALSE
    set_sandbox_mode("yes")
    assert is_sandbox_mode() is False


def test_set_institute_valid_and_urls():
    set_institute("csiontario")
    assert config.site_url() == "https://apps.csiontario.ca"
    assert config.auth_url() == "https://apps.csiontario.ca/o/authorize/"
    assert config.token_url() == "https://apps.csiontario.ca/o/token/"
    assert config.userinfo_url() == "https://apps.csiontario.ca/api/csiauth/me/"


def test_set_institute_rejects_unknown():
    with pytest.raises(ValueError):
        set_institute("csiquebec")
