"""Example CSIAPPS Streamlit app.

Run with:  uv run streamlit run examples/streamlit_app.py
"""

import streamlit as st

import csiapps
from csiapps.streamlit import page_wrapper


def _seed_sandbox() -> None:
    csiapps.set_institute("csiontario")
    if csiapps.fetch_org_options():
        return
    csiapps.create_sport_org("Rowing Canada", id=100)
    csiapps.create_sport_org("Swim BC", id=200)
    csiapps.create_profile(5, 100)
    csiapps.create_profile(3, 200)


def dashboard() -> None:
    _seed_sandbox()
    st.title("Athletes")
    organisations = csiapps.fetch_org_options()
    org = st.selectbox(
        "Organisation",
        options=list(organisations),
        format_func=organisations.get,
        index=None,
        placeholder="Choose an organisation",
    )
    if org:
        profiles = csiapps.fetch_profiles(filters={"sport_org_id": int(org)})
        st.dataframe(
            [csiapps.flatten_profile(profile) for profile in profiles],
            width="stretch",
        )


page_wrapper(dashboard)
