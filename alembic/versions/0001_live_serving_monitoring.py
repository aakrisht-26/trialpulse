"""Create the live, serving and monitoring schemas with empty tables (CLAUDE.md Step 3).

- live: canonical versions from API v2 (the same columns as the offline versions table, see
  trialpulse.contracts.versions), their normalized texts, each trial's current state, status
  transitions, and quarantined records.
- serving: the latest score per trial and the change-only score history (Section 12).
- monitoring: pipeline runs, graded predictions and promotion decisions (Sections 12 and 14).

No table holds a person's name or contact details (Section 2).

Revision ID: 0001
Revises:
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = ("live", "serving", "monitoring")
NOW = sa.text("now()")


def _canonical_columns() -> list[sa.Column]:
    """The canonical version columns, frozen as of this revision (a test compares them with
    trialpulse.contracts.versions.COLUMN_TYPES)."""
    text, date = sa.Text, sa.Date
    columns: list[sa.Column] = [
        sa.Column("nct_id", text, nullable=False),
        sa.Column("nct_version", sa.BigInteger),  # set for rows bootstrapped from the dataset
        sa.Column("source", text, nullable=False),
        sa.Column("effective_date", date, nullable=False),
        sa.Column("effective_date_type", text),
        sa.Column("submitted_date", date),
        sa.Column("overall_status", text, nullable=False),
        sa.Column("study_type", text),
        sa.Column("study_first_post_date", date),
        sa.Column("study_first_post_date_type", text),
        sa.Column("study_first_submit_date", date),
        sa.Column("status_verified_date", date),
    ]
    for name in ("start_date", "primary_completion_date", "completion_date"):
        columns += [
            sa.Column(name, date),
            sa.Column(f"{name}_precision", text),
            sa.Column(f"{name}_type", text),
        ]
    columns += [
        sa.Column("allocation", text),
        sa.Column("intervention_model", text),
        sa.Column("primary_purpose", text),
        sa.Column("masking", text),
        sa.Column("healthy_volunteers", sa.Boolean),
        sa.Column("sex", text),
        sa.Column("minimum_age_years", sa.Double),
        sa.Column("maximum_age_years", sa.Double),
        sa.Column("enrollment_count", sa.BigInteger),
        sa.Column("enrollment_type", text),
        sa.Column("lead_sponsor_class", text),
        sa.Column("organization_class", text),
        sa.Column("lead_sponsor_name", text),  # empty for individual sponsors
        sa.Column("sponsor_key", text),
        sa.Column("sponsor_is_individual", sa.Boolean, nullable=False),
    ]
    for field in ("brief_title", "official_title", "brief_summary", "eligibility_criteria"):
        columns.append(sa.Column(f"{field}_hash", text))
    columns += [
        sa.Column("why_stopped_hash", text),
        sa.Column("content_hash", text, nullable=False),
    ]
    return columns


def _run_id() -> sa.Column:
    return sa.Column("run_id", sa.BigInteger, sa.ForeignKey("monitoring.pipeline_runs.run_id"))


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    op.create_table(
        "pipeline_runs",
        sa.Column("run_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("job", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("watermark_from", sa.Date),
        sa.Column("watermark_to", sa.Date),
        sa.Column("records_fetched", sa.Integer),
        sa.Column("versions_added", sa.Integer),
        sa.Column("records_quarantined", sa.Integer),
        sa.Column("trials_rescored", sa.Integer),
        sa.Column("details", postgresql.JSONB),
        sa.Column("error", sa.Text),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="run_status"),
        schema="monitoring",
    )

    op.create_table(
        "study_versions",
        *_canonical_columns(),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        _run_id(),
        sa.PrimaryKeyConstraint("nct_id", "effective_date", "content_hash"),
        schema="live",
    )
    op.create_index(
        "study_versions_effective_date", "study_versions", ["effective_date"], schema="live"
    )
    op.create_table(
        "texts",
        sa.Column("text_hash", sa.Text, primary_key=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema="live",
    )
    op.create_table(
        "trial_state",
        sa.Column("nct_id", sa.Text, primary_key=True),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("overall_status", sa.Text, nullable=False),
        sa.Column("study_type", sa.Text),
        sa.Column("study_first_post_date", sa.Date),
        sa.Column("versions_known", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["nct_id", "effective_date", "content_hash"],
            [
                "live.study_versions.nct_id",
                "live.study_versions.effective_date",
                "live.study_versions.content_hash",
            ],
        ),
        schema="live",
    )
    op.create_table(
        "outcome_events",
        sa.Column("event_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("nct_id", sa.Text, nullable=False),
        sa.Column("from_status", sa.Text),
        sa.Column("to_status", sa.Text, nullable=False),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        _run_id(),
        sa.UniqueConstraint("nct_id", "effective_date", "to_status"),
        schema="live",
    )
    op.create_table(
        "quarantine",
        sa.Column("quarantine_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("nct_id", sa.Text),
        sa.Column("effective_date", sa.Date),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column("reasons", sa.Text, nullable=False),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        _run_id(),
        schema="live",
    )

    risk = "BETWEEN 0 AND 1"
    op.create_table(
        "trial_scores_latest",
        sa.Column("nct_id", sa.Text, primary_key=True),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("landmark_index", sa.SmallInteger),
        sa.Column("model_name", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("risk_12m", sa.Double, nullable=False),
        sa.Column("risk_24m", sa.Double, nullable=False),
        sa.Column("drivers", postgresql.JSONB, nullable=False),  # top 5 SHAP drivers
        sa.Column("content_hash", sa.Text),  # the version that was scored
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(f"risk_12m {risk} AND risk_24m {risk}", name="latest_risk_range"),
        schema="serving",
    )
    op.create_table(
        "trial_score_history",
        sa.Column("history_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("nct_id", sa.Text, nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("model_name", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("risk_12m", sa.Double, nullable=False),
        sa.Column("risk_24m", sa.Double, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(f"risk_12m {risk} AND risk_24m {risk}", name="history_risk_range"),
        sa.CheckConstraint(
            "reason IN ('first_score', 'score_moved', 'record_changed')", name="history_reason"
        ),
        sa.UniqueConstraint("nct_id", "as_of_date", "model_version"),
        schema="serving",
    )

    op.create_table(
        "graded_predictions",
        sa.Column("nct_id", sa.Text, nullable=False),
        sa.Column("prediction_date", sa.Date, nullable=False),
        sa.Column("horizon_months", sa.SmallInteger, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("predicted_risk", sa.Double, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("outcome_date", sa.Date),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("nct_id", "prediction_date", "horizon_months", "model_version"),
        sa.CheckConstraint(
            "outcome IN ('early_stop', 'completed', 'open', 'censored')", name="graded_outcome"
        ),
        schema="monitoring",
    )
    op.create_table(
        "promotions",
        sa.Column("promotion_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("champion_version", sa.Text),
        sa.Column("challenger_version", sa.Text, nullable=False),
        sa.Column("window_start", sa.Date),
        sa.Column("window_end", sa.Date),
        sa.Column("champion_auc_12m", sa.Double),
        sa.Column("challenger_auc_12m", sa.Double),
        sa.Column("challenger_calibration_slope", sa.Double),
        sa.Column("promoted", sa.Boolean, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("mlflow_run_id", sa.Text),
        schema="monitoring",
    )


def downgrade() -> None:
    for schema, table in (
        ("monitoring", "promotions"),
        ("monitoring", "graded_predictions"),
        ("serving", "trial_score_history"),
        ("serving", "trial_scores_latest"),
        ("live", "quarantine"),
        ("live", "outcome_events"),
        ("live", "trial_state"),
        ("live", "texts"),
        ("live", "study_versions"),
        ("monitoring", "pipeline_runs"),
    ):
        op.drop_table(table, schema=schema)
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema}")
