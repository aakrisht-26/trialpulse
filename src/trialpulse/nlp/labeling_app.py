"""Streamlit app for labeling the gold set (CLAUDE.md Section 11).

    uv run streamlit run src/trialpulse/nlp/labeling_app.py

- Serves all test texts first, then the dev texts.
- One click per text: clicking a label saves it to labels/gold_labels.csv (ids, text
  hashes and labels only) and moves to the next unlabeled text. "Back" steps to the
  previous text to revise it; "Next unlabeled" returns to where labeling stopped.
- Test texts are blind: no suggestion is ever shown for them. Dev texts show the suggestion
  from data/nlp/dev_suggestions.csv, and each saved label records whether a suggestion was
  shown (the assisted column).

TRIALPULSE_GOLD_SAMPLE, TRIALPULSE_GOLD_LABELS and TRIALPULSE_GOLD_SUGGESTIONS override the
paths.
"""

import os
from pathlib import Path
from typing import Literal

import streamlit as st

from trialpulse.nlp.gold import (
    LABELS_PATH,
    SAMPLE_PATH,
    SUGGESTIONS_PATH,
    GoldItem,
    load_dev_suggestions,
    load_sample,
    next_unlabeled,
    read_labels,
    save_label,
    serving_order,
    suggestion_for,
)
from trialpulse.nlp.taxonomy import DEFINITIONS, LABELS, TIE_BREAK_RULES

DISCLAIMER = "Research demo. Not medical advice. Not for patient decision-making."
CURRENT = "current_item"  # session key: the item being revisited, if any


def _paths() -> tuple[Path, Path, Path]:
    sample = Path(os.environ.get("TRIALPULSE_GOLD_SAMPLE", SAMPLE_PATH))
    labels = Path(os.environ.get("TRIALPULSE_GOLD_LABELS", LABELS_PATH))
    suggestions = Path(os.environ.get("TRIALPULSE_GOLD_SUGGESTIONS", SUGGESTIONS_PATH))
    return sample, labels, suggestions


def _sidebar() -> None:
    st.sidebar.header("Labels")
    for label in LABELS:
        st.sidebar.markdown(f"**{label}**: {DEFINITIONS[label]}")
    st.sidebar.header("Tie-break rules")
    for rule in TIE_BREAK_RULES:
        st.sidebar.markdown(f"- {rule}")
    st.sidebar.caption("Full guide: docs/labeling_guide.md")


def _show(
    item: GoldItem,
    labels_path: Path,
    saved: str | None,
    suggestion: str | None,
    position: int,
    total: int,
) -> None:
    st.subheader(item.nct_id)
    st.caption(f"Text {position + 1} of {total}. {item.status}, stop year {item.stop_year}.")
    st.text_area("why_stopped", item.why_stopped, height=160, disabled=True)
    if suggestion is not None:
        st.info(f"Suggestion: {suggestion}")
    if saved is not None:
        st.caption(f"Saved label: {saved}")
    columns = st.columns(4)
    for n, label in enumerate(LABELS):
        kind: Literal["primary", "secondary"] = "primary" if label == saved else "secondary"
        if columns[n % 4].button(label, key=f"label-{label}", type=kind, width="stretch"):
            save_label(labels_path, item, label, assisted=suggestion is not None)
            st.session_state.pop(CURRENT, None)
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="TrialPulse gold labeling")
    st.title("Gold set labeling")
    st.caption(DISCLAIMER)
    sample_path, labels_path, suggestions_path = _paths()
    if not sample_path.is_file():
        st.error(
            f"No gold sample at {sample_path}. Build it first: "
            "uv run python -m trialpulse.nlp.gold --build"
        )
        return
    items = serving_order(load_sample(sample_path))
    labels = read_labels(labels_path)
    suggestions = load_dev_suggestions(suggestions_path, items)
    done = sum(1 for i in items if i.key in labels)
    st.progress(done / len(items) if items else 1.0, text=f"{done} of {len(items)} labeled")
    _sidebar()
    if items:
        st.caption(f"Texts: ClinicalTrials.gov current records ({items[0].source}).")

    keys = [i.key for i in items]
    revisit = st.session_state.get(CURRENT)
    item = items[keys.index(revisit)] if revisit in keys else next_unlabeled(items, labels)
    position = keys.index(item.key) if item is not None else len(items)

    back, forward = st.columns(2)
    if back.button("Back", key="back", disabled=position == 0):
        st.session_state[CURRENT] = keys[position - 1]
        st.rerun()
    if forward.button("Next unlabeled", key="next", disabled=revisit not in keys):
        st.session_state.pop(CURRENT, None)
        st.rerun()

    if item is None:
        st.success("Every item is labeled.")
        return
    saved = labels.get(item.key, {}).get("label")
    _show(item, labels_path, saved, suggestion_for(item, suggestions), position, len(items))


main()
