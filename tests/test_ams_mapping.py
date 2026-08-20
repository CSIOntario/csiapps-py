import pandas as pd
import pytest

import csiapps
from csiapps import client

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "vendor", "vendor_profile_id", "vendor_profile_name"],
    "properties": {
        "id": {"type": "number"},
        "vendor": {"type": "string"},
        "vendor_profile_id": {"type": "string"},
        "vendor_profile_name": {"type": "string"},
    },
}
RECORDS = [
    {
        "id": 1,
        "vendor": "VendorX",
        "vendor_profile_id": "VX-1",
        "vendor_profile_name": "Ada N.",
    },
    {
        "id": 2,
        "vendor": "VendorX",
        "vendor_profile_id": "VX-2",
        "vendor_profile_name": "Blair O.",
    },
]


def test_fetch_ams_mapping_returns_four_column_sandbox_dataframe():
    csiapps.register_sandbox_schema("ams-test", SCHEMA)
    csiapps.make_request(
        "api/warehouse/ingestion/primary/",
        method="POST",
        body={"source": "ams-test", "records": RECORDS, "subject_field": "id"},
        sandbox=True,
    )

    actual = csiapps.fetch_ams_mapping("ams-test", sandbox=True)

    assert isinstance(actual, pd.DataFrame)
    pd.testing.assert_frame_equal(actual, pd.DataFrame(RECORDS))


def test_fetch_ams_mapping_reduces_paginated_production_history(monkeypatch):
    pages = [
        {
            "results": [
                {
                    "id": 1,
                    "updated_at": "2026-01-01T00:00:00Z",
                    "data": {
                        "id": 1,
                        "vendor": "VendorX",
                        "vendor_profile_id": "VX-1",
                        "vendor_profile_name": "Ada",
                        "active": True,
                    },
                },
                {
                    "id": 2,
                    "updated_at": "2026-01-02T00:00:00Z",
                    "data": {
                        "id": 1,
                        "vendor": "VendorX",
                        "vendor_profile_id": "VX-1",
                        "vendor_profile_name": "Ada",
                        "active": False,
                    },
                },
                {
                    "id": 3,
                    "updated_at": "2026-01-03T00:00:00Z",
                    "data": {
                        "id": 2,
                        "vendor": "VendorX",
                        "vendor_profile_id": "VX-1",
                        "vendor_profile_name": "Ada corrected",
                        "active": True,
                    },
                },
            ]
        },
        {
            "results": [
                {
                    "id": 4,
                    "updated_at": "2026-01-04T00:00:00Z",
                    "data": {
                        "id": 3,
                        "vendor": "VendorY",
                        "vendor_profile_id": "VY-3",
                        "vendor_profile_name": "Cai",
                    },
                },
                {
                    "id": 10,
                    "updated_at": "2026-01-05T00:00:00Z",
                    "data": {
                        "id": 4,
                        "vendor": "VendorY",
                        "vendor_profile_id": "VY-4",
                        "vendor_profile_name": "Devon",
                        "active": True,
                    },
                },
                {
                    "id": 11,
                    "updated_at": "2026-01-05T00:00:00Z",
                    "data": {
                        "id": 4,
                        "vendor": "VendorY",
                        "vendor_profile_id": "VY-4",
                        "vendor_profile_name": "Devon",
                        "active": False,
                    },
                },
            ]
        },
    ]
    monkeypatch.setattr(client, "make_request", lambda *args, **kwargs: pages)

    actual = csiapps.fetch_ams_mapping("prod-ams", token="token", sandbox=False)

    expected = pd.DataFrame(
        [
            {
                "id": 2,
                "vendor": "VendorX",
                "vendor_profile_id": "VX-1",
                "vendor_profile_name": "Ada corrected",
            },
            {
                "id": 3,
                "vendor": "VendorY",
                "vendor_profile_id": "VY-3",
                "vendor_profile_name": "Cai",
            },
        ]
    )
    pd.testing.assert_frame_equal(actual, expected)


def test_fetch_ams_mapping_handles_empty_malformed_and_environment_uuid(monkeypatch):
    seen = {}

    def empty_request(*args, **kwargs):
        seen.update(kwargs)
        return [{"results": []}]

    monkeypatch.setenv("AMS_MAPPING_UUID", "env-ams")
    monkeypatch.setattr(client, "make_request", empty_request)
    empty = csiapps.fetch_ams_mapping(sandbox=False)
    assert list(empty.columns) == ["id", "vendor", "vendor_profile_id", "vendor_profile_name"]
    assert empty.empty
    assert seen["query"] == {"source_uuid": "env-ams"}

    monkeypatch.setattr(
        client,
        "make_request",
        lambda *args, **kwargs: [
            {
                "results": [
                    {
                        "id": 1,
                        "updated_at": "2026-01-01T00:00:00Z",
                        "data": {"id": 1, "vendor": "VendorX", "vendor_profile_id": "VX-1"},
                    }
                ]
            }
        ],
    )
    with pytest.raises(ValueError, match=r"record 1.*vendor_profile_name"):
        csiapps.fetch_ams_mapping("prod-ams", sandbox=False)
    with pytest.raises(ValueError, match="AMS_MAPPING_UUID"):
        csiapps.fetch_ams_mapping("", sandbox=True)
