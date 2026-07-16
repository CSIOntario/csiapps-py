"""Example: schema -> ingest -> retrieve, entirely in the local sandbox.

Run with:  uv run python examples/warehouse_ingest.py
"""

import csiapps


def main():
    csiapps.register_sandbox_schema(
        "demo",
        {
            "type": "object",
            "required": ["id", "firstName"],
            "properties": {"id": {"type": "string"}, "firstName": {"type": "string"}},
        },
    )

    csiapps.make_request(
        "api/warehouse/ingestion/primary/",
        method="POST",
        body={
            "source": "demo",
            "records": [{"id": "a1", "firstName": "Ada"}, {"id": "b2", "firstName": "Blair"}],
            "subject_field": "id",
        },
    )

    page = csiapps.make_request("api/warehouse/data-records", query={"source_uuid": "demo"})
    assert page["count"] == 2, page
    print(f"retrieved {page['count']} record(s):")
    for rec in page["results"]:
        print("  ", rec["data"])


if __name__ == "__main__":
    main()
