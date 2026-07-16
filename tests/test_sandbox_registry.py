"""Ported from tests/testthat/test-sandbox-registry.R plus the fetch_* sandbox
message cases from test-sandbox-wrapper.R: create_sport_org, create_profile, and
the fetch_* helpers reading the dummy registry.
"""

import pytest

from csiapps import (
    create_profile,
    create_sport_org,
    fetch_org_options,
    fetch_profile,
    fetch_profiles,
    make_request,
    register_sandbox_schema,
    set_sandbox_mode,
)


def fn(n):
    return [f"First{i}" for i in range(1, n + 1)]


def ln(n):
    return [f"Last{i}" for i in range(1, n + 1)]


# ---- create_sport_org --------------------------------------------------


def test_create_sport_org_requires_name():
    with pytest.raises(TypeError):
        create_sport_org()
    with pytest.raises(ValueError):
        create_sport_org("")


def test_create_sport_org_generates_id_in_range():
    org = create_sport_org("Rowing Canada")
    assert 1 <= org["id"] <= 999
    assert org["name"] == "Rowing Canada"


def test_create_sport_org_honours_id_and_rejects_collision():
    create_sport_org("Swim BC", id=321)
    with pytest.raises(ValueError, match="already exists"):
        create_sport_org("Swim BC", id=321)


def test_create_sport_org_id_bounds():
    assert create_sport_org("Two digit", id=42)["id"] == 42
    assert create_sport_org("Max", id=999)["id"] == 999
    with pytest.raises(ValueError):
        create_sport_org("Too big", id=1000)
    with pytest.raises(ValueError):
        create_sport_org("Zero", id=0)


def test_create_sport_org_rejects_invalid_ids():
    with pytest.raises(ValueError):
        create_sport_org("Rowing Canada", id=-5)
    with pytest.raises(ValueError):
        create_sport_org("Rowing Canada", id=1.5)


# ---- create_profile ----------------------------------------------------


def test_create_profile_requires_existing_org():
    with pytest.raises(ValueError, match="does not exist"):
        create_profile(2, sport_org_id=999, first_names=fn(2), last_names=ln(2))


def test_create_profile_uses_supplied_names():
    create_sport_org("Rowing Canada", id=100)
    create_profile(2, 100, first_names=["Ada", "Blair"], last_names=["Lovelace", "Okafor"])
    p = fetch_profile(profile_id=1, sandbox=True)
    assert p["person"]["first_name"] == "Ada"
    assert p["person"]["last_name"] == "Lovelace"


def test_create_profile_rejects_half_or_mismatched_names():
    create_sport_org("Rowing Canada", id=100)
    with pytest.raises(ValueError, match="both"):
        create_profile(2, 100, first_names=["Ada", "Blair"])
    with pytest.raises(ValueError):
        create_profile(2, 100, first_names=["Ada"], last_names=["Lovelace", "Okafor"])


def test_create_profile_generates_unique_names():
    create_sport_org("Rowing Canada", id=100)
    create_profile(20, 100)
    profs = fetch_profiles(sandbox=True)
    full = [f"{p['person']['first_name']} {p['person']['last_name']}" for p in profs]
    assert len(full) == 20
    assert len(set(full)) == 20  # every athlete name distinct


def test_athlete_sport_name_is_org_name():
    org = create_sport_org("Rowing Canada", id=100)
    create_profile(1, 100, first_names=fn(1), last_names=ln(1))
    p = fetch_profile(profile_id=1, sandbox=True)
    assert p["sport"]["name"] == org["name"]


# ---- fetch_* in sandbox mode -------------------------------------------


def test_fetch_org_options_returns_label_value_pairs():
    create_sport_org("Cycling Canada", id=100)
    opts = fetch_org_options(sandbox=True)
    assert len(opts) == 1
    assert opts[0] == {"label": "Cycling Canada", "value": 100}


def test_fetch_profiles_filtered_by_sport_org_id():
    create_sport_org("Rowing Canada", id=100)
    create_sport_org("Swim BC", id=200)
    create_profile(3, 100, first_names=fn(3), last_names=ln(3))
    create_profile(2, 200, first_names=fn(2), last_names=ln(2))

    assert len(fetch_profiles(sandbox=True)) == 5
    only_100 = fetch_profiles(filters={"sport_org_id": 100}, sandbox=True)
    assert len(only_100) == 3
    p = only_100[0]
    assert p["person"]["first_name"]
    assert p["person"]["last_name"]
    assert p["sport"]["id"] == 100


def test_fetch_profile_by_id_or_none():
    create_sport_org("Rowing Canada", id=100)
    create_profile(2, 100, first_names=fn(2), last_names=ln(2))
    assert fetch_profile(profile_id=1, sandbox=True)["id"] == 1
    assert fetch_profile(profile_id=999, sandbox=True) is None


def test_empty_registry_yields_empty_reads():
    assert fetch_org_options(sandbox=True) == []
    assert fetch_profiles(sandbox=True) == []
    assert fetch_profile(profile_id=1, sandbox=True) is None


# ---- subject linkage via subject_field ---------------------------------


def _athlete_schema():
    return {
        "type": "object",
        "required": ["athlete_id"],
        "properties": {"athlete_id": {"type": "integer"}},
    }


def test_records_link_to_registered_athlete():
    create_sport_org("Rowing Canada", id=100)
    create_profile(1, 100, first_names=fn(1), last_names=ln(1))  # athlete id 1
    register_sandbox_schema("src-1", _athlete_schema())
    make_request(
        "api/warehouse/ingestion/primary/",
        method="POST",
        body={
            "source": "src-1",
            "records": [{"athlete_id": 1}, {"athlete_id": 999}],
            "subject_field": "athlete_id",
        },
        sandbox=True,
    )
    page = make_request("api/warehouse/data-records", query={"source_uuid": "src-1"}, sandbox=True)
    assert page["results"][0]["subject"]["first_name"]
    assert page["results"][0]["subject"]["id"] == 1
    assert page["results"][1]["subject"] is None


def test_subjects_resolve_at_read_time_backfill():
    create_sport_org("Rowing Canada", id=100)
    create_profile(1, 100, first_names=fn(1), last_names=ln(1))  # athlete id 1
    register_sandbox_schema("src-bf", _athlete_schema())
    # ingest a record for athlete 2, who does not exist yet
    make_request(
        "api/warehouse/ingestion/primary/",
        method="POST",
        body={"source": "src-bf", "records": [{"athlete_id": 2}], "subject_field": "athlete_id"},
        sandbox=True,
    )
    before = make_request(
        "api/warehouse/data-records", query={"source_uuid": "src-bf"}, sandbox=True
    )
    assert before["results"][0]["subject"] is None

    create_profile(1, 100, first_names=fn(1), last_names=ln(1))  # now athlete id 2
    after = make_request(
        "api/warehouse/data-records", query={"source_uuid": "src-bf"}, sandbox=True
    )
    assert after["results"][0]["subject"]["id"] == 2


# ---- warn outside sandbox mode -----------------------------------------


def test_create_helpers_warn_outside_sandbox():
    set_sandbox_mode(False)
    with pytest.warns(UserWarning, match="sandbox"):
        create_sport_org("Rowing Canada", id=100)
    with pytest.warns(UserWarning, match="sandbox"):
        create_profile(1, 100, first_names=fn(1), last_names=ln(1))
