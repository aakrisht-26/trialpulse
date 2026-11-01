"""Portfolio: find a sponsor by name, then its open trials ranked by early-stop risk.

Only sponsors with a display name can be found (ADR 0047); flagged trials stay in place with
a flag column and a "Hide flagged" switch (ADR 0055).
"""

from urllib.parse import quote

import streamlit as st
from dashboard import ui

PAGE_SIZE = 200  # the API's largest page


def main(page: ui.Page) -> None:
    prefix = st.text_input("Sponsor name starts with", key="prefix", max_chars=100).strip()
    if len(prefix) < 3:
        st.info("Type at least 3 characters of the sponsor's name.")
        return
    found = page.get("/v1/sponsors", {"prefix": prefix}).body["sponsors"]
    if not found:
        st.info("No sponsor with a public portfolio has a name that starts like this.")
        return
    labels = {ui.sponsor_label(s["name"], s["sponsor_class"]): s["name"] for s in found}
    chosen = st.selectbox("Sponsor", list(labels), key="sponsor")
    name = labels[str(chosen)]
    first = page.get(f"/v1/sponsors/{quote(name, safe='')}/portfolio",
                     {"limit": PAGE_SIZE, "offset": 0}, allow=(404,))  # fmt: skip
    if first.status == 404:
        st.info("No sponsor with a public portfolio has this name.")
        return
    total = first.body["trials_total"]
    pages = max(1, -(-total // PAGE_SIZE))
    number = 1
    if pages > 1:
        number = int(st.number_input(f"Page (of {pages})", 1, pages, 1, key="page_number"))
    answer = first if number == 1 else page.get(
        f"/v1/sponsors/{quote(name, safe='')}/portfolio",
        {"limit": PAGE_SIZE, "offset": (number - 1) * PAGE_SIZE},
    )  # fmt: skip
    body = answer.body
    st.subheader(ui.sponsor_label(body["sponsor_name"], body["sponsor_class"]))
    st.caption(
        f"{total:,} open trials, ranked by early-stop risk at 12 months; trials without a "
        "score follow, with their reason."
    )
    ui.show_trials(body["trials"], key="portfolio")


ui.run(
    main,
    "Portfolio",
    "Search for a sponsor by the start of its name to see its open trials ranked by "
    "early-stop risk at 12 months. Sponsors that are individuals are not listed.",
)
