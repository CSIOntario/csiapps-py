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
csiapps.create_profile(5, 100)

app_ui = csiapps.ui_wrapper(
    ui.h3("Athletes"),
    ui.output_ui("athletes"),
)


def app_server(input, output, session):
    @render.ui
    def athletes():
        rows = []
        for p in csiapps.fetch_profiles():
            person, sport = p["person"], p["sport"]
            label = f"{person['first_name']} {person['last_name']} — {sport['name']}"
            rows.append(ui.tags.li(label))
        return ui.tags.ul(*rows)


app = App(app_ui, csiapps.server_wrapper(app_server))
