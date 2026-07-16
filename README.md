# csiapps (Python)

Python port of the CSIO [`csiapps`](https://github.com/CSIOntario/csiapps) R
package. Helper functions and utilities for CSI data warehouse ingestion and
[Shiny for Python](https://shiny.posit.co/py/) web applications.

Full feature parity with the R package: the API client (`make_request`,
`fetch_*`), the local sandbox (schema → ingest → retrieve, plus a dummy
registration registry), and the Shiny app wrappers (`ui_wrapper`,
`server_wrapper`). **Sandbox mode is on by default** so nothing hits production
by accident.

## Installation

```bash
pip install csiapps
```

## Quickstart

```python
import csiapps

csiapps.register_sandbox_schema("demo", {
    "type": "object", "required": ["id"],
    "properties": {"id": {"type": "string"}},
})
csiapps.make_request("api/warehouse/ingestion/primary/", method="POST",
    body={"source": "demo", "records": [{"id": "a1"}], "subject_field": "id"})
page = csiapps.make_request("api/warehouse/data-records", query={"source_uuid": "demo"})
print(page["count"])   # 1
```

See [`docs/usage.md`](docs/usage.md) and runnable [`examples/`](examples/)
(`warehouse_ingest.py`, `app.py`).

## Development

Uses [uv](https://docs.astral.sh/uv/).

```bash
uv sync                        # install deps + dev tools
uv run pytest                  # run tests
uv run ruff check .            # lint
uv run --group docs mkdocs serve   # preview docs
uv build                       # build sdist + wheel
```
