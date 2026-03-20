"""SEPA Pipeline Dashboard — main entry point."""

import streamlit as st

st.set_page_config(
    page_title="SEPA Pipeline",
    page_icon=":bar_chart:",
    layout="wide",
)

st.title("SEPA Pipeline Dashboard")
st.markdown(
    "Monitor the health of the ETL pipeline and explore retail price trends "
    "from Argentina's SEPA price transparency system."
)

st.sidebar.success("Select a page above.")
