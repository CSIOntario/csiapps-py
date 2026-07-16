"""Example CSIAPPS Shiny app running in sandbox mode.

Run with:  uv run shiny run --reload examples/app.py

Sandbox mode is on by default, so this needs no credentials and no network. The
login is simulated; set CSIAPPS_ACCESS_TOKEN to have the header show your real
/me identity.
"""

from shiny import App, render, ui

import csiapps

# Seed dummy registration data (sandbox is the default mode).
csiapps.set_institute("csiontario")
csiapps.create_sport_org("Rowing Canada", id=100)
csiapps.create_sport_org("Swim BC", id=200)
csiapps.create_profile(5, 100)
csiapps.create_profile(3, 200)

app_ui = csiapps.ui_wrapper(
    # fetch_org_options() is a {value: label} dict -> feeds input_select directly.
    ui.input_select("org", "Organisation", choices=csiapps.fetch_org_options()),
    ui.h3("Athletes"),
    ui.output_ui("athletes"),
)


def app_server(input, output, session):
    @render.ui
    def athletes():
        profiles = csiapps.fetch_profiles(filters={"sport_org_id": int(input.org())})
        rows = []
        for p in (csiapps.flatten_profile(x) for x in profiles):
            rows.append(ui.tags.li(f"{p['first_name']} {p['last_name']} — {p['sport']}"))
        return ui.tags.ul(*rows)


app = App(app_ui, csiapps.server_wrapper(app_server))
