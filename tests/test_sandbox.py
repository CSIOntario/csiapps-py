"""Ported from tests/testthat/test-sandbox.R (schema register/retrieve, ingest,
retrieve, clear, error routes) plus the make_request routing cases from
test-sandbox-mode.R.

Validator wording is adapted from Ajv to jsonschema (Draft 7): minLength reads
"too short" rather than "fewer than 10 characters" (see PORTING_PLAN parity note).
"""

import json
import re

import pytest

from csiapps import (
    browse_sandbox,
    clear_sandbox,
    make_request,
    register_sandbox_schema,
    sandbox,
)

# ---- register_sandbox_schema -------------------------------------------


def test_register_from_dict_string_and_file(test_schema, tmp_path):
    register_sandbox_schema("from-dict", test_schema)
    register_sandbox_schema("from-string", json.dumps(test_schema))
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(test_schema))
    register_sandbox_schema("from-file", str(path))

    for uuid in ("from-dict", "from-string", "from-file"):
        res = make_request(f"api/warehouse/data-sources/{uuid}", sandbox=True)
        assert res["head_primary_definition"]["schema"]["title"] == "A registration form"


def test_reregister_overwrites(test_schema):
    register_sandbox_schema("overwrite", test_schema)
    v2 = {**test_schema, "title": "Version 2"}
    register_sandbox_schema("overwrite", v2)
    res = make_request("api/warehouse/data-sources/overwrite", sandbox=True)
    assert res["head_primary_definition"]["schema"]["title"] == "Version 2"


def test_register_rejects_invalid_args(test_schema):
    with pytest.raises((ValueError, TypeError)):
        register_sandbox_schema(42, test_schema)
    with pytest.raises(ValueError):
        register_sandbox_schema("", test_schema)
    with pytest.raises(ValueError, match="must be a list"):
        register_sandbox_schema("ok", 42)


# ---- schema retrieval route --------------------------------------------


def test_schema_retrieval_shape(test_schema):
    register_sandbox_schema("shape", test_schema)
    res = make_request("api/warehouse/data-sources/shape", sandbox=True)
    assert res["uuid"] == "shape"
    assert isinstance(res["head_primary_definition"], dict)
    assert res["head_primary_definition"]["schema"] == test_schema


def test_schema_retrieval_tolerates_slashes(test_schema):
    register_sandbox_schema("slashes", test_schema)
    for ep in (
        "api/warehouse/data-sources/slashes",
        "/api/warehouse/data-sources/slashes",
        "api/warehouse/data-sources/slashes/",
        "/api/warehouse/data-sources/slashes/",
    ):
        assert make_request(ep, sandbox=True)["uuid"] == "slashes"


def test_unregistered_schema_is_404():
    with pytest.raises(RuntimeError, match=r"API request failed \(404\).*register_sandbox_schema"):
        make_request("api/warehouse/data-sources/nope", sandbox=True)


# ---- ingestion route ---------------------------------------------------


def test_valid_ingest_201_shape(quiet_ingest):
    res = quiet_ingest("ingest-ok")
    assert set(res) == {"dataset", "created_records"}
    assert res["created_records"] == 2
    assert res["dataset"]["source"] == "ingest-ok"
    assert res["dataset"]["uuid"]


def test_ingest_accepts_lowercase_method(test_schema, test_records):
    register_sandbox_schema("lower", test_schema)
    res = make_request(
        "api/warehouse/ingestion/primary/",
        method="post",
        body={"source": "lower", "records": test_records},
        sandbox=True,
    )
    assert res["created_records"] == 2


def test_schema_violations_rejected_with_indices_and_detail(test_schema):
    register_sandbox_schema("invalid", test_schema)
    bad = [
        {"id": "a", "firstName": "Too", "lastName": "Short", "telephone": "123"},  # minLength
        {"firstName": "No", "lastName": "Id"},  # missing required
        {"id": "c", "firstName": "Bad", "lastName": "Age", "age": "thirty"},  # wrong type
    ]
    with pytest.raises(RuntimeError) as exc:
        make_request(
            "api/warehouse/ingestion/primary/",
            method="POST",
            body={"source": "invalid", "records": bad},
            sandbox=True,
        )
    msg = str(exc.value)
    assert re.search(r"API request failed \(400\)", msg)
    assert "record(s) 1, 2, 3" in msg
    assert "too short" in msg  # jsonschema minLength detail
    assert "required" in msg  # jsonschema required detail


def test_batch_is_all_or_nothing(test_schema, test_records):
    register_sandbox_schema("atomic", test_schema)
    mixed = [*test_records, {"firstName": "No", "lastName": "Id"}]
    with pytest.raises(RuntimeError, match=r"record\(s\) 3"):
        make_request(
            "api/warehouse/ingestion/primary/",
            method="POST",
            body={"source": "atomic", "records": mixed},
            sandbox=True,
        )
    page = make_request(
        "api/warehouse/data-records", query={"source_uuid": "atomic"}, sandbox=True
    )
    assert page["count"] == 0


def test_ingest_validates_required_body_fields(test_schema, test_records):
    register_sandbox_schema("body-check", test_schema)
    with pytest.raises(RuntimeError, match=r"API request failed \(400\)"):
        make_request(
            "api/warehouse/ingestion/primary/",
            method="POST",
            body={"records": test_records},
            sandbox=True,
        )
    with pytest.raises(RuntimeError, match=r"API request failed \(400\)"):
        make_request(
            "api/warehouse/ingestion/primary/",
            method="POST",
            body={"source": "body-check"},
            sandbox=True,
        )
    with pytest.raises(RuntimeError, match="no records provided"):
        make_request(
            "api/warehouse/ingestion/primary/",
            method="POST",
            body={"source": "body-check", "records": []},
            sandbox=True,
        )


def test_ingest_unregistered_source_is_404(test_records):
    with pytest.raises(RuntimeError, match=r"API request failed \(404\)"):
        make_request(
            "api/warehouse/ingestion/primary/",
            method="POST",
            body={"source": "ghost", "records": test_records},
            sandbox=True,
        )


def test_payloads_written_one_folder_per_source(quiet_ingest):
    quiet_ingest("disk-a")
    quiet_ingest("disk-a", records=[{"id": "z", "firstName": "Zoe", "lastName": "Zed"}])
    quiet_ingest("disk-b")

    import os

    dir_a = os.path.join(sandbox.sandbox_dir(), "disk-a")
    dir_b = os.path.join(sandbox.sandbox_dir(), "disk-b")
    files_a = [f for f in os.listdir(dir_a) if re.match(r"^payload_.*\.json$", f)]
    files_b = [f for f in os.listdir(dir_b) if re.match(r"^payload_.*\.json$", f)]
    assert len(files_a) == 2
    assert len(files_b) == 1

    reread = json.loads(open(os.path.join(dir_b, files_b[0])).read())
    assert len(reread) == 2
    assert reread[0]["id"] == "xxxx"


def test_single_required_field_enforced():
    register_sandbox_schema(
        "single-req",
        '{"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}',
    )
    with pytest.raises(RuntimeError, match=r"API request failed \(400\)"):
        make_request(
            "api/warehouse/ingestion/primary/",
            method="POST",
            body={"source": "single-req", "records": [{"other": "x"}]},
            sandbox=True,
        )


# ---- retrieval route ---------------------------------------------------


def test_retrieved_records_use_envelope_shape(quiet_ingest, test_records):
    ingest_res = quiet_ingest("envelope")
    page = make_request(
        "api/warehouse/data-records", query={"source_uuid": "envelope"}, sandbox=True
    )
    assert list(page) == ["count", "next", "previous", "results"]
    assert page["count"] == 2
    assert len(page["results"]) == 2

    rec = page["results"][0]
    assert list(rec) == ["id", "dataset_uuid", "data", "subject", "created_at", "updated_at"]
    assert rec["subject"] is None
    assert rec["dataset_uuid"] == ingest_res["dataset"]["uuid"]
    assert rec["data"] == test_records[0]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", rec["created_at"])


def test_record_ids_increment_across_ingests(quiet_ingest):
    quiet_ingest("ids")
    quiet_ingest("ids", records=[{"id": "z", "firstName": "Zoe", "lastName": "Zed"}])
    page = make_request("api/warehouse/data-records", query={"source_uuid": "ids"}, sandbox=True)
    assert [r["id"] for r in page["results"]] == [1, 2, 3]


def test_paginate_returns_list_of_pages(quiet_ingest):
    quiet_ingest("pages")
    pages = make_request(
        "api/warehouse/data-records", query={"source_uuid": "pages"}, paginate=True, sandbox=True
    )
    assert len(pages) == 1
    assert pages[0]["count"] == 2
    assert pages[0]["next"] is None


def test_empty_source_returns_empty_page():
    page = make_request(
        "api/warehouse/data-records", query={"source_uuid": "never-ingested"}, sandbox=True
    )
    assert page["count"] == 0
    assert page["results"] == []


def test_retrieval_without_source_uuid_is_400():
    with pytest.raises(RuntimeError, match=r"API request failed \(400\).*source_uuid"):
        make_request("api/warehouse/data-records", sandbox=True)


# ---- clear_sandbox / browse_sandbox ------------------------------------


def test_targeted_clear_removes_one_source(quiet_ingest):
    import os

    quiet_ingest("keep")
    quiet_ingest("drop")
    clear_sandbox("drop")

    assert not os.path.isdir(os.path.join(sandbox.sandbox_dir(), "drop"))
    with pytest.raises(RuntimeError, match="404"):
        make_request("api/warehouse/data-sources/drop", sandbox=True)
    kept = make_request("api/warehouse/data-records", query={"source_uuid": "keep"}, sandbox=True)
    assert kept["count"] == 2


def test_global_clear_wipes_everything(quiet_ingest):
    import os

    quiet_ingest("wipe-1")
    quiet_ingest("wipe-2")
    clear_sandbox()

    assert [e for e in os.listdir(sandbox.sandbox_dir()) if os.path.isdir(
        os.path.join(sandbox.sandbox_dir(), e))] == []
    for uuid in ("wipe-1", "wipe-2"):
        with pytest.raises(RuntimeError, match="404"):
            make_request(f"api/warehouse/data-sources/{uuid}", sandbox=True)
        page = make_request(
            "api/warehouse/data-records", query={"source_uuid": uuid}, sandbox=True
        )
        assert page["count"] == 0


def test_clearing_unregistered_source_does_not_error():
    clear_sandbox("never-existed")  # no raise


def test_browse_errors_without_payload_folder():
    with pytest.raises(RuntimeError, match="does not exist"):
        browse_sandbox("no-such-source")


# ---- unsupported endpoints + default routing ---------------------------


def test_unsupported_endpoints_are_501():
    with pytest.raises(RuntimeError) as exc:
        make_request("api/csiauth/me/", sandbox=True)
    msg = str(exc.value)
    assert re.search(r"API request failed \(501\)", msg)
    assert "not emulated" in msg
    assert "api/warehouse/ingestion/primary" in msg

    with pytest.raises(RuntimeError, match="501"):
        make_request("api/warehouse/data-records", method="POST", sandbox=True)
    with pytest.raises(RuntimeError, match="501"):
        make_request("api/warehouse/ingestion/primary/", method="GET", sandbox=True)


def test_defaults_route_to_sandbox(test_schema):
    # nothing configured -> sandbox on by default (no explicit sandbox= arg)
    register_sandbox_schema("default-route", test_schema)
    res = make_request("api/warehouse/data-sources/default-route")
    assert res["head_primary_definition"]["schema"]["title"] == "A registration form"
