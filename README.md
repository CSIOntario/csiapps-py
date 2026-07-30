# csiapps (Python)

Python port of the CSIO [`csiapps`](https://github.com/CSIOntario/csiapps-r) R
package. Helper functions and utilities for CSI data warehouse ingestion and
[Shiny for Python](https://shiny.posit.co/py/) or [Dash](https://dash.plotly.com/)
web applications.

Full feature parity with the R package: the API client (`make_request`,
`fetch_*`), the local sandbox (schema → ingest → retrieve, plus a dummy
registration registry), and per-framework app wrappers for Shiny
([`csiapps.shiny`](#shiny-apps): `ui_wrapper`, `server_wrapper`) and Dash
([`csiapps.dash`](#dash-apps): `layout_wrapper`, `attach`). **Sandbox mode is on
by default** so nothing hits production by accident.

The core — client, sandbox, and OAuth2 PKCE helpers — is framework-independent
and depends on neither framework, so `import csiapps` pulls in no web framework
at all. The two frameworks are symmetric, mutually exclusive optional extras:
each app installs and imports only the one it uses.

## Installation

```bash
pip install csiapps            # core only: ingestion + sandbox, no web framework
```

For an app, add the extra for your framework:

```bash
pip install 'csiapps[shiny]'   # Shiny app
pip install 'csiapps[dash]'    # Dash app
```

> **Migrating from 0.2.x (breaking):** Shiny is no longer a hard dependency and
> the wrappers moved off the top-level package. A Shiny app now installs
> `csiapps[shiny]` and imports `from csiapps.shiny import ui_wrapper,
> server_wrapper` instead of `from csiapps import ui_wrapper, server_wrapper`.
> The core API (`make_request`, `fetch_*`, `token_ready`, the sandbox helpers)
> is unchanged.

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

## Shiny apps

Install `csiapps[shiny]` and wrap the UI and server. `ui_wrapper` adds the CSI
navbar, footer, auth-status line and sandbox banner; `server_wrapper` runs the
OAuth2 PKCE login (simulated in sandbox mode) and stores the per-session token
so `fetch_*` helpers resolve it automatically.

```python
from shiny import App, reactive, ui
from csiapps.shiny import server_wrapper, ui_wrapper
import csiapps

app_ui = ui_wrapper(
    ui.input_select("org", "Organisation", choices={}),
)

def app_server(input, output, session):
    @reactive.effect
    def _load_orgs():             # gates itself until login; no token handling
        ui.update_select("org", choices=csiapps.fetch_org_options())

app = App(app_ui, server_wrapper(app_server))
```

See the runnable [`examples/app.py`](examples/app.py).

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
`CSIAPPS_REDIRECT_URI` (pointing at `/redirect`), and
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
uv sync                        # install deps + dev tools (both framework extras)
uv run pytest                  # run tests (framework suites skip if their extra is absent)
uv run ruff check .            # lint
uv build                       # build sdist + wheel

# Prove the isolation locally, matching the CI jobs:
uv sync --no-group shiny-tests --no-group dash-tests && uv run --no-sync pytest  # core only
uv sync --no-group dash-tests  && uv run --no-sync pytest                        # Shiny only
uv sync --no-group shiny-tests && uv run --no-sync pytest                        # Dash only
uv sync                                                                          # restore both
```

The documentation site lives in its own repo,
[`csiapps`](https://github.com/CSIOntario/csiapps) — there is no `mkdocs.yml`
here. Its Python API reference autodocs from this package's docstrings on
`main`, so a docstring change lands on the site the next time that repo is
pushed. To preview, clone it alongside this one and run `mkdocs serve` there.

Shipping a change to the package? Follow
[Releasing csiapps](https://csiontario.github.io/csiapps/releasing/).
