# csiapps (Python)

Python port of the CSIO [`csiapps`](https://github.com/CSIOntario/csiapps-r) R
package. Helper functions and utilities for CSI data warehouse ingestion and
[Shiny for Python](https://shiny.posit.co/py/) or [Dash](https://dash.plotly.com/)
web applications.

Full feature parity with the R package: the API client (`make_request`,
`fetch_*`), the local sandbox (schema → ingest → retrieve, plus a dummy
registration registry), and the Shiny app wrappers (`ui_wrapper`,
`server_wrapper`). **Sandbox mode is on by default** so nothing hits production
by accident.

Everything except the app wrappers is framework-independent, so the same client,
sandbox and OAuth2 PKCE helpers back both the Shiny wrappers and the Dash ones
in [`csiapps.dash`](#dash-apps).

## Installation

```bash
pip install csiapps
```

For a Dash app, install the optional dependencies too:

```bash
pip install 'csiapps[dash]'
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

## Dash apps

`attach()` puts the OAuth2 PKCE flow in front of every route, so the redirect
happens before Dash renders and callbacks never run for an unauthenticated user.
No `dcc.Interval` login bounce, no per-page `Authorization` header.

```python
from dash import Dash, Input, Output, callback, dcc
from csiapps.dash import attach, layout_wrapper
import csiapps

app = Dash(__name__)
attach(app)                       # auth guard + chrome; no-op in sandbox mode

app.layout = layout_wrapper(      # CSI navbar, footer, sandbox banner
    dcc.Dropdown(id="org", options=csiapps.fetch_org_options()),
)

@callback(Output("people", "data"), Input("org", "value"))
def load(org):                    # no token handling; the helper resolves it
    return [csiapps.flatten_profile(p)
            for p in csiapps.fetch_profiles(filters={"sport_org_id": org})]
```

In production set `CSIAPPS_CLIENT_ID`, `CSIAPPS_CLIENT_SECRET`,
`CSIAPPS_REDIRECT_URI` (pointing at `/csi-auth/redirect`), and
`CSIAPPS_SECRET_KEY` — a stable ≥32-character value that signs the session
cookie. `attach()` raises at startup if it is missing, since the alternative is
an unexplained login loop.

Sandbox mode skips the guard entirely, so a Dash app is fully developable with
no credentials and no network. Deploy sandbox apps with `--workers 1`: the
sandbox registry is per-process, so extra workers each invent their own dummy
athletes.

See the [cross-language documentation](https://csiontario.github.io/csiapps/)
(R and Python side by side, plus a [parity checklist](https://csiontario.github.io/csiapps/parity/))
and the runnable [`examples/`](examples/) (`warehouse_ingest.py`, `app.py`,
`dash_app.py`).

## Development

Uses [uv](https://docs.astral.sh/uv/).

```bash
uv sync                        # install deps + dev tools (incl. the dash extra)
uv run pytest                  # run tests
uv run ruff check .            # lint
uv sync --no-group dash-tests && uv run --no-sync pytest   # the no-Dash install
uv run --group docs mkdocs serve   # preview docs
uv build                       # build sdist + wheel
```
