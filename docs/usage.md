# Usage

## Warehouse workflow in the sandbox

Register a schema, ingest records, and read them back — all local, no network:

```python
import csiapps

csiapps.register_sandbox_schema("demo", {
    "type": "object",
    "required": ["id", "firstName"],
    "properties": {"id": {"type": "string"}, "firstName": {"type": "string"}},
})

csiapps.make_request(
    "api/warehouse/ingestion/primary/",
    method="POST",
    body={"source": "demo", "records": [{"id": "a1", "firstName": "Ada"}], "subject_field": "id"},
)

page = csiapps.make_request("api/warehouse/data-records", query={"source_uuid": "demo"})
print(page["count"], page["results"][0]["data"])
```

A runnable version is in [`examples/warehouse_ingest.py`](https://github.com/CSIOntario/csiapps-py/blob/main/examples/warehouse_ingest.py).

## Dummy registration data

Seed sport orgs and athletes so `fetch_org_options` / `fetch_profiles` behave
like production:

```python
org = csiapps.create_sport_org("Rowing Canada", id=100)
csiapps.create_profile(5, org["id"])          # 5 athletes with random names

csiapps.fetch_org_options()                    # {100: "Rowing Canada"}
csiapps.fetch_profiles(filters={"sport_org_id": 100})   # 5 profiles (nested dicts)
```

`fetch_org_options()` returns a `{value: label}` dict, so it plugs straight into
a select input:

```python
ui.input_select("org", "Organisation", choices=csiapps.fetch_org_options())
```

## Showing results in a table

`fetch_profiles()` returns deeply nested dicts. Flatten them to scalar rows with
`flatten_profile`, then wrap in a DataFrame (pandas or polars — your dashboard's
choice, not a `csiapps` dependency):

```python
import pandas as pd
from shiny import render

@render.data_frame
def athletes():
    rows = [csiapps.flatten_profile(p) for p in csiapps.fetch_profiles()]
    return render.DataGrid(pd.DataFrame(rows))
```

## Talking to the real API

Turn sandbox off and provide a token (per-session inside a Shiny app, or the
`CSIAPPS_ACCESS_TOKEN` environment variable outside one):

```python
csiapps.set_institute("csiontario")
csiapps.set_sandbox_mode(False)
profiles = csiapps.fetch_profiles(token="…")
```

## Shiny app

Wrap your app's UI and server:

```python
from shiny import App, render, ui
import csiapps

csiapps.create_sport_org("Rowing Canada", id=100)
csiapps.create_profile(5, 100)

app_ui = csiapps.ui_wrapper(ui.output_ui("athletes"))

def app_server(input, output, session):
    @render.ui
    def athletes():
        rows = [f"{p['person']['first_name']} {p['person']['last_name']}"
                for p in csiapps.fetch_profiles()]
        return ui.tags.ul(*[ui.tags.li(r) for r in rows])

app = App(app_ui, csiapps.server_wrapper(app_server))
```

Run it with `shiny run --reload examples/app.py`. In sandbox mode the login is
simulated (seeded from `CSIAPPS_ACCESS_TOKEN` if present); for deployment, set
`CSIAPPS_ENV=production` to use the real OAuth2 flow.
