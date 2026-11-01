"""TrialPulse dashboard (Step 16): the entry point and its navigation.

    uv run streamlit run app/Home.py

Every page calls the API only (`dashboard.client`), at the address in Streamlit's secrets
(`API_URL`); it holds no database, no model and no data.
"""

import streamlit as st

st.set_page_config(page_title="TrialPulse", page_icon=":material/monitor_heart:", layout="wide")

PAGES = [
    st.Page("pages/overview.py", title="Overview", icon=":material/home:", default=True),
    st.Page("pages/portfolio.py", title="Portfolio", icon=":material/work:",
            url_path="portfolio"),
    st.Page("pages/trial.py", title="Trial detail", icon=":material/timeline:",
            url_path="trial"),
    st.Page("pages/landscape.py", title="Landscape", icon=":material/category:",
            url_path="landscape"),
    st.Page("pages/reasons.py", title="Why trials stop", icon=":material/help:",
            url_path="why-trials-stop"),
    st.Page("pages/model_health.py", title="Model health", icon=":material/monitoring:",
            url_path="model-health"),
]  # fmt: skip

st.navigation(PAGES).run()
