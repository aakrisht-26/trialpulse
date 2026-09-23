"""The stop-reason taxonomy (CLAUDE.md Section 11). docs/labeling_guide.md explains each
label with examples; the operational and scientific groups come from config/project.yaml."""

from typing import Literal

from trialpulse.config import ProjectConfig

LABELS: tuple[str, ...] = (
    "accrual",
    "business",
    "funding",
    "administrative",
    "covid19",
    "safety",
    "efficacy",
    "other",
)

DEFINITIONS: dict[str, str] = {
    "accrual": "Recruitment or enrollment problems: too slow, too few eligible participants, "
    "or a target that could not be reached.",
    "business": "A sponsor or company decision, including portfolio or strategic changes, "
    "when no more specific reason is given.",
    "funding": "Money ran out, was withdrawn, or was never secured.",
    "administrative": "Operational or organizational reasons: the investigator left, the site "
    "closed, contracts, regulatory paperwork, drug supply, or study-team capacity.",
    "covid19": "The COVID-19 pandemic, unless another reason clearly dominates.",
    "safety": "Harm or a safety signal, including a data monitoring committee stop for harm.",
    "efficacy": "Lack of benefit or futility, including futility at an interim analysis, or "
    "early proof of benefit.",
    "other": "Any reason outside the labels above, or text too vague to classify.",
}

TIE_BREAK_RULES: tuple[str, ...] = (
    "When several reasons are given, label the decisive one: the reason the text presents as "
    "causing the stop.",
    "A COVID-19 mention is covid19 unless another reason clearly dominates.",
    "A sponsor decision with no further detail is business.",
    "An interim analysis showing futility is efficacy; a stop for harm is safety.",
    "Label only what the text says. Do not infer a reason from the trial's other fields.",
)

Group = Literal["operational", "scientific", "other"]


def validate_label(label: str) -> str:
    if label not in LABELS:
        raise ValueError(f"unknown label {label!r}; expected one of {LABELS}")
    return label


def group_of(label: str, cfg: ProjectConfig) -> Group:
    """Operational or scientific, as defined in config/project.yaml, otherwise other."""
    validate_label(label)
    if label in cfg.stop_reasons.operational:
        return "operational"
    if label in cfg.stop_reasons.scientific:
        return "scientific"
    return "other"
