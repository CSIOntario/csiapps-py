import pytest

from csiapps import config

# Env vars the R suite reset to NA with withr::local_envvar; we clear them so
# each test starts from a known-clean configuration.
_ENV_VARS = (
    "CSIAPPS_ENV",
    "CSIAPPS_ACCESS_TOKEN",
    "CSIAPPS_REDIRECT_URI",
    "CSIAPPS_CLIENT_ID",
    "CSIAPPS_CLIENT_SECRET",
    "CSIAPPS_SCOPE",
)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Reset module state + relevant env vars between tests (≈ withr locals)."""
    config._state["institute"] = "csipacific"
    config._state["sandbox_override"] = None
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
