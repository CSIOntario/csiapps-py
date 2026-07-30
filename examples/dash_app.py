"""Example CSIAPPS Dash app running in sandbox mode.

Run with:  uv run python examples/dash_app.py

Sandbox mode is on by default, so this needs no credentials and no network: the
auth guard is skipped entirely and the data comes from the local registry seeded
below. Set CSIAPPS_ACCESS_TOKEN to have the header show your real /me identity.

To run it against production instead:

    export CSIAPPS_ENV=production
    export CSIAPPS_CLIENT_ID=...  CSIAPPS_CLIENT_SECRET=...
    export CSIAPPS_REDIRECT_URI=https://your-app/redirect
    export CSIAPPS_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"

Nothing else changes: no dcc.Interval login bounce, no dcc.Location, no manual
Authorization header. attach() puts the OAuth2 PKCE flow in front of every route
and the fetch_* helpers pick the token up on their own.
"""

from dash import Dash, Input, Output, callback, dash_table, dcc, html

import csiapps
from csiapps.dash import attach, layout_wrapper

# Seed dummy registration data (sandbox is the default mode).
csiapps.set_institute("csiontario")
csiapps.create_sport_org("Rowing Canada", id=100)
csiapps.create_sport_org("Swim BC", id=200)
csiapps.create_profile(5, 100)
csiapps.create_profile(3, 200)

app = Dash(__name__)
attach(app)

# Unlike the Shiny example, the organisation choices *can* be fetched at layout
# time: layout_wrapper returns a callable that Dash invokes on every page load,
# so this runs per-request, inside the request context, with the logged-in
# user's token already resolved.
app.layout = layout_wrapper(
    html.H3("Athletes"),
    dcc.Dropdown(id="org", options=csiapps.fetch_org_options(), placeholder="Organisation"),
    dash_table.DataTable(id="people", page_size=10),
)


@callback(Output("people", "data"), Input("org", "value"))
def load_people(org):
    # No token handling: behind attach()'s guard this callback only ever runs for
    # an authenticated user, and fetch_profiles() resolves the token itself.
    if not org:
        return []
    profiles = csiapps.fetch_profiles(filters={"sport_org_id": int(org)})
    return [csiapps.flatten_profile(p) for p in profiles]


if __name__ == "__main__":
    # Single process, so the per-process sandbox registry seeded above is the
    # same one every request sees.
    app.run(debug=True, port=8050)
