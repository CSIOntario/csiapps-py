"""Example CSIAPPS Shiny app running in sandbox mode.

Run with:  uv run shiny run --reload examples/app.py

Sandbox mode is on by default, so this needs no credentials and no network. The
login is simulated; set CSIAPPS_ACCESS_TOKEN to have the header show your real
/me identity.
"""

from shiny import App, reactive, render, ui

import csiapps

# Seed dummy registration data (sandbox is the default mode).
csiapps.set_institute("csiontario")
csiapps.create_sport_org("Rowing Canada", id=100)
csiapps.create_sport_org("Swim BC", id=200)
csiapps.create_profile(5, 100)
csiapps.create_profile(3, 200)

# The organisation choices are fetched per-session inside the server, NOT at
# UI-construction time: fetch_org_options() needs the logged-in user's token,
# which only exists once a session has authenticated. Start the select empty and
# fill it in when the data arrives.
app_ui = csiapps.ui_wrapper(
    ui.input_select("org", "Organisation", choices={}),
    ui.h3("Athletes"),
    ui.output_ui("athletes"),
)


def app_server(input, output, session):
    # Populate the organisation dropdown once the token is available. The helper
    # gates itself until login completes, so this effect simply re-runs when the
    # token arrives -- no manual token check needed. fetch_org_options() returns
    # a {value: label} dict, exactly what update_select expects.
    @reactive.effect
    def _load_orgs():
        ui.update_select("org", choices=csiapps.fetch_org_options())

    @render.ui
    def athletes():
        if not input.org():
            return ui.tags.ul()
        profiles = csiapps.fetch_profiles(filters={"sport_org_id": int(input.org())})
        rows = []
        for p in (csiapps.flatten_profile(x) for x in profiles):
            rows.append(ui.tags.li(f"{p['first_name']} {p['last_name']} — {p['sport']}"))
        return ui.tags.ul(*rows)


app = App(app_ui, csiapps.server_wrapper(app_server))
