import pytest

from csiapps import config, make_request, register_sandbox_schema
from csiapps import sandbox as _sandbox

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
    _sandbox.clear_sandbox()  # ≈ local_clean_sandbox()
    yield
    _sandbox.clear_sandbox()


# ---- shared sandbox fixtures (ported from helper-sandbox.R) ----


@pytest.fixture
def test_schema():
    return {
        "title": "A registration form",
        "type": "object",
        "required": ["id", "firstName", "lastName"],
        "properties": {
            "id": {"type": "string"},
            "firstName": {"type": "string"},
            "lastName": {"type": "string"},
            "age": {"type": "integer"},
            "telephone": {"type": "string", "minLength": 10},
        },
    }


@pytest.fixture
def test_records():
    return [
        {"id": "xxxx", "firstName": "John", "lastName": "Doe", "age": 30,
         "telephone": "1234567890"},
        {"id": "yyyy", "firstName": "Jane", "lastName": "Smith", "age": 25,
         "telephone": "0987654321"},
    ]


@pytest.fixture
def quiet_ingest(test_schema, test_records):
    """Register a schema and ingest records; returns the ingest response."""

    def _ingest(uuid, records=None, schema=None):
        register_sandbox_schema(uuid, schema if schema is not None else test_schema)
        return make_request(
            "api/warehouse/ingestion/primary/",
            method="POST",
            body={
                "source": uuid,
                "records": records if records is not None else test_records,
                "subject_field": "id",
            },
            sandbox=True,
        )

    return _ingest
