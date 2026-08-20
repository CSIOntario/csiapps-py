"""Serialization and setup boundaries for csiapps.quarto."""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

pytest.importorskip("shiny", reason="csiapps[quarto] not installed")

from csiapps import quarto  # noqa: E402


def test_datasets_json_preserves_complete_typed_records():
    payload = quarto._datasets_json(
        {
            "athletes": pd.DataFrame(
                {
                    "id": [1, 2**60],
                    "name": ["Ada", "Zoë"],
                    "score": [float("nan"), 9.5],
                    "date": [date(2026, 8, 19), date(2026, 8, 20)],
                    "seen": [datetime(2026, 8, 20, tzinfo=timezone.utc)] * 2,
                }
            )
        }
    )

    assert '"id":"1152921504606846976"' in payload
    assert '"name":"Zoë"' in payload
    assert '"score":null' in payload
    assert '"date":"2026-08-19"' in payload
    assert '"seen":"2026-08-20T00:00:00+00:00"' in payload


@pytest.mark.parametrize(
    "datasets, message",
    [
        ([], "mapping"),
        ({"athletes": [1, 2]}, "DataFrame or list of records"),
        ({"": []}, "non-empty strings"),
    ],
)
def test_datasets_json_rejects_ambiguous_registries(datasets, message):
    with pytest.raises((TypeError, ValueError), match=message):
        quarto._datasets_json(datasets)


def test_quarto_setup_requires_callable_and_active_session(monkeypatch):
    with pytest.raises(TypeError, match="zero-argument callable"):
        quarto.quarto_setup([])
    monkeypatch.setattr(quarto, "_get_current_session", lambda: None)
    with pytest.raises(RuntimeError, match="active Shiny session"):
        quarto.quarto_setup()


def test_quarto_setup_is_a_noop_during_quarto_render(monkeypatch):
    monkeypatch.setattr(quarto, "_get_current_session", lambda: None)
    monkeypatch.setenv("QUARTO_DOCUMENT_PATH", ".")
    assert quarto.quarto_setup() is None
