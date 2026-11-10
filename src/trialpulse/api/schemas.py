"""Response models of the API (CLAUDE.md Section 13). Every response carries the notice, the
ADR 0008 attribution and the data-as-of date (`Envelope`)."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

NOTICE = "Research demo. Not medical advice. Not for patient decision-making."
SOURCE_NOTE = (
    "Source: ClinicalTrials.gov. TrialPulse normalizes the registry records and adds derived "
    "early-stop risk scores and drivers, which are not part of ClinicalTrials.gov."
)


class Attribution(BaseModel):
    source: str = "ClinicalTrials.gov"
    url: str = "https://clinicaltrials.gov"
    note: str = SOURCE_NOTE


class Envelope(BaseModel):
    notice: str = NOTICE
    attribution: Attribution = Field(default_factory=Attribution)
    data_as_of: dt.date | None = Field(
        None, description="The date ClinicalTrials.gov processed the data the scores rest on."
    )


class Problem(Envelope):
    detail: str


class Health(Envelope):
    status: Literal["ok"] = "ok"


class Driver(BaseModel):
    feature: str
    value: str
    contribution: float = Field(description="SHAP value in log-odds; its sign is the direction")
    direction: Literal["raises", "lowers"]


class Score(BaseModel):
    as_of_date: dt.date = Field(description="The date this trial was last scored")
    risk_12m: float
    risk_24m: float
    flagged: bool
    flag: str | None = Field(None, description='"outside the validated range" when flagged')
    flag_note: str | None = None
    landmark_index: int
    true_landmark_index: int
    model_version: str
    drivers: dict[str, list[Driver]]


class NotScored(BaseModel):
    as_of_date: dt.date
    reason: str
    reason_text: str


class TrialRisk(Envelope):
    nct_id: str
    brief_title: str | None
    sponsor_name: str | None = Field(description="Null for a sponsor without a display name")
    sponsor_class: str | None
    areas: list[str]
    status: Literal["scored", "not_scored"]
    score: Score | None
    not_scored: NotScored | None
    risk_language: str = "Scores describe elevated early-stop risk, not whether a trial will fail."


class TimelinePoint(BaseModel):
    as_of_date: dt.date
    risk_12m: float
    risk_24m: float
    flagged: bool
    reasons: list[str]
    model_version: str


class Marker(BaseModel):
    post_date: dt.date
    changed: list[str]


class Timeline(Envelope):
    nct_id: str
    points: list[TimelinePoint]
    markers: list[Marker]
    note: str = (
        "The first point is the trial's score at the data cutoff, regenerated when the live "
        "system started (or its first live score, for a trial registered later); there are no "
        "scores before it. After it, a point is written when a score moves by 0.01 or more, "
        "the record changes, a landmark passes or the model changes. Markers before the first "
        "point show amendments only (ADR 0043)."
    )


class SponsorName(BaseModel):
    name: str
    sponsor_class: str | None


class SponsorSearch(Envelope):
    prefix: str
    sponsors: list[SponsorName]


class PortfolioTrial(BaseModel):
    nct_id: str
    brief_title: str | None
    status: Literal["scored", "not_scored"]
    as_of_date: dt.date
    risk_12m: float | None
    risk_24m: float | None
    flagged: bool | None
    flag: str | None
    reason: str | None


class Portfolio(Envelope):
    sponsor_name: str
    sponsor_class: str | None
    trials_total: int
    offset: int
    limit: int
    trials: list[PortfolioTrial]


class AreaCount(BaseModel):
    slug: str
    name: str
    mesh_tree: str | None
    open_trials: int


class AreaList(Envelope):
    areas: list[AreaCount]
    open_trials: int = Field(description="Distinct open trials; area totals can exceed it")
    note: str


class Landscape(Envelope):
    slug: str
    name: str
    open_trials: int = Field(description="Open trials in the area today (descriptive)")
    scored: int
    flagged: int
    not_scored: dict[str, int]
    mean_risk_12m: float | None
    median_risk_12m: float | None
    mean_risk_24m: float | None
    median_risk_24m: float | None
    top_trials: list[PortfolioTrial]
    note: str


class ReasonCount(BaseModel):
    month: dt.date
    reason: str
    sponsor_class: str
    n: int


class ReasonSummary(Envelope):
    labeled_through: dt.date | None
    early_stops_after_labels: int | None = Field(
        description="Early stops recorded after the labels' date: counted, not labeled"
    )
    totals: dict[str, int]
    counts: list[ReasonCount]
    note: str


class Grade(BaseModel):
    prediction_date: dt.date
    horizon_months: int
    source: str = Field(description="'live' or 'replay' (ADR 0079)")
    model_version: str
    n_rows: int
    n_early_stop: int
    n_completed: int
    n_censored: int
    n_open: int
    mean_risk: float | None
    auc: float | None
    brier: float | None
    calibration_slope: float | None
    calibration_intercept: float | None
    lift_top_10: float | None
    matured: bool = Field(description="Whether the horizon has passed (else partial-horizon)")


class Performance(Envelope):
    grades: list[Grade]
    note: str


class DriftColumn(BaseModel):
    column_name: str
    kind: str
    method: str
    statistic: float | None
    threshold: float
    drifted: bool
    n_current: int


class Drift(Envelope):
    week_date: dt.date | None
    features_drifted_share: float | None
    columns: list[DriftColumn]
    note: str


class Promotion(BaseModel):
    decided_at: dt.datetime
    champion_version: str | None
    challenger_version: str
    window_start: dt.date | None
    window_end: dt.date | None
    champion_auc_12m: float | None
    challenger_auc_12m: float | None
    challenger_calibration_slope: float | None
    promoted: bool
    reason: str


class Promotions(Envelope):
    decisions: list[Promotion]
    note: str


class ModelInfo(Envelope):
    model_version: str | None
    statement: dict[str, object] | None
    scored_trials: int
    flagged_share: float | None = Field(description="Share of served scores that are flagged")
    not_scored: dict[str, int]
