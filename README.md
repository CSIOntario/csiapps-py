# csiapps (Python)

Python port of the CSIO [`csiapps`](https://github.com/CSIOntario/csiapps) R
package. Helper functions and utilities for CSI data warehouse ingestion and
[Shiny for Python](https://shiny.posit.co/py/) web applications.

> **Status:** Phase 1 (scaffold). The functional modules land phase by phase —
> see [`PORTING_PLAN.md`](PORTING_PLAN.md).

## Development

Uses [uv](https://docs.astral.sh/uv/).

```bash
uv sync          # create .venv and install deps + dev tools
uv run pytest    # run tests
uv run ruff check .
```

## Installation (once published)

```bash
pip install csiapps
```
