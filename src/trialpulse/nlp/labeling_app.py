"""Streamlit app for labeling the gold set (CLAUDE.md Section 11).

    uv run streamlit run src/trialpulse/nlp/labeling_app.py

Shows one why_stopped text at a time, with the taxonomy in the sidebar. Each label is
saved at once to labels/gold_labels.csv (ids, text hashes and labels only), so labeling can stop and
resume at any point. TRIALPULSE_GOLD_SAMPLE and TRIALPULSE_GOLD_LABELS override the paths.
"""

import os
from pathlib import Path

import streamlit as st

from trialpulse.nlp.gold import (
    LABELS_PATH,
    SAMPLE_PATH,
    GoldItem,
    load_sample,
    next_unlabeled,
    read_labels,
    save_label,
)
from trialpulse.nlp.taxonomy import DEFINITIONS, LABELS, TIE_BREAK_RULES

DISCLAIMER = "Research demo. Not medical advice. Not for patient decision-making."


def _paths() -> tuple[Path, Path]:
    sample = Path(os.environ.get("TRIALPULSE_GOLD_SAMPLE", SAMPLE_PATH))
    labels = Path(os.environ.get("TRIALPULSE_GOLD_LABELS", LABELS_PATH))
    return sample, labels


def _sidebar() -> None:
    st.sidebar.header("Labels")
    for label in LABELS:
        st.sidebar.markdown(f"**{label}**: {DEFINITIONS[label]}")
    st.sidebar.header("Tie-break rules")
    for rule in TIE_BREAK_RULES:
        st.sidebar.markdown(f"- {rule}")
    st.sidebar.caption("Full guide: docs/labeling_guide.md")


def _show(item: GoldItem, labels_path: Path, current: str | None) -> None:
    st.subheader(item.nct_id)
    st.caption(f"{item.status}, stop year {item.stop_year}")
    st.text_area("why_stopped", item.why_stopped, height=160, disabled=True)
    index = LABELS.index(current) if current in LABELS else None
    choice = st.radio("Label", LABELS, index=index, horizontal=True, key=f"r-{item.key}")
    if st.button("Save label", type="primary", disabled=choice is None):
        assert choice is not None
        save_label(labels_path, item, choice)
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="TrialPulse gold labeling")
    st.title("Gold set labeling")
    st.caption(DISCLAIMER)
    sample_path, labels_path = _paths()
    if not sample_path.is_file():
        st.error(
            f"No gold sample at {sample_path}. Build it first: "
            "uv run python -m trialpulse.nlp.gold --build"
        )
        return
    items = load_sample(sample_path)
    labels = read_labels(labels_path)
    done = sum(1 for i in items if i.key in labels)
    st.progress(done / len(items) if items else 1.0, text=f"{done} of {len(items)} labeled")
    _sidebar()
    if items:
        st.caption(f"Texts: ClinicalTrials.gov current records ({items[0].source}).")

    labeled = [i for i in items if i.key in labels]
    revise = st.selectbox(
        "Revise a labeled item (optional)",
        [None, *labeled],
        format_func=lambda i: "" if i is None else f"{i.nct_id} ({i.text_sha256[:8]})",
    )
    item = revise or next_unlabeled(items, labels)
    if item is None:
        st.success("Every item is labeled.")
        return
    _show(item, labels_path, labels.get(item.key, {}).get("label"))


main()
