"""Compact live state, compact serving rows and aggregate grades (ADRs 0037, 0039, 0043, 0045).

- live: `trial_state` becomes the wide row of ADR 0037 (latest-record fields features read,
  the version-0 values, the version count, ever suspended, recent post dates, the lapse
  date, a reversal flag); `study_versions` becomes compact API v2 rows keyed by trial, post
  date and feature hash; `texts` goes (no text is stored); `sponsor_file` holds the resumable
  sponsor pass; `quarantine` gets a unique payload hash; `outcome_events` gets the change of
  state's kind (LAPSED and REOPENED besides the registry's statuses).
- serving: compact rows (ADR 0037 decision 6). Drivers become four arrays (horizon in
  months, feature number, the value as text, the contribution to 3 decimals, whose sign is
  the direction); hashes become 32 bytes; history loses the model name (its version names
  it) and gains `run_id` (ADR 0045). Existing rows are converted, both ways. New:
  `amendment_markers` (ADR 0043).
- monitoring: `graded_predictions` holds aggregates (ADR 0039); `score_files` holds one
  compressed score file per month; `pipeline_runs` gains the API's data timestamp, the kind
  of run and one running job at a time (a partial unique index).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")
# Feature numbers of the compact drivers: positions in the registry's main features as of
# this revision (trialpulse.features.registry.model_columns; a test holds them equal).
FEATURE_NUMBERS: tuple[str, ...] = (
    "allocation", "intervention_model", "primary_purpose", "masking", "healthy_volunteers",
    "sex", "minimum_age_years", "maximum_age_years", "enrollment_count",
    "planned_duration_months", "start_anticipated", "registered_after_start",
    "registration_lag_months", "registration_year", "title_phase", "n_interventions",
    "n_intervention_types", "intervention_drug", "intervention_procedure",
    "intervention_other", "intervention_biological", "intervention_device",
    "intervention_behavioral", "intervention_radiation", "intervention_dietary_supplement",
    "intervention_diagnostic_test", "intervention_genetic", "intervention_combination_product",
    "status", "n_versions", "n_versions_recent", "months_since_last_update",
    "primary_completion_slip_months", "completion_slip_months", "start_slip_months",
    "enrollment_closed", "enrollment_target_ratio", "enrolled_to_first_target_ratio",
    "ever_suspended", "currently_suspended", "start_overdue", "primary_completion_overdue",
    "primary_completion_reached", "months_since_status_verified", "sponsor_class",
    "organization_class", "sponsor_has_identity", "sponsor_prior_registrations",
    "sponsor_prior_ended", "sponsor_prior_stopped", "sponsor_stop_rate",
    "open_interventional_trials", "eligibility_n_inclusion", "eligibility_n_exclusion",
    "eligibility_length", "eligibility_n_thresholds", "eligibility_age_span_years",
    *(f"eligibility_svd_{i:02d}" for i in range(1, 33)),
    *(f"summary_svd_{i:02d}" for i in range(1, 33)),
    "landmark_index", "months_since_t0",
)  # fmt: skip
RISK = "BETWEEN 0 AND 1"
NOT_SCORED = ("reversal", "beyond_120_months", "band_not_validated")
REASONS = ("first_score", "record_change", "landmark_passed", "new_model", "calendar")


def _compact_fields() -> list[sa.Column]:
    """The compact row of trialpulse.live.contract.LIVE_FIELDS, frozen as of this revision."""
    text, date = sa.Text, sa.Date
    return [
        sa.Column("effective_date", date, nullable=False),
        sa.Column("status", text, nullable=False),
        sa.Column("study_type", text),
        sa.Column("study_first_post_date", date),
        sa.Column("status_verified_date", date),
        sa.Column("start_date", date),
        sa.Column("primary_completion_date", date),
        sa.Column("primary_completion_date_precision", text),
        sa.Column("primary_completion_date_type", text),
        sa.Column("completion_date", date),
        sa.Column("completion_date_precision", text),
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
        sa.Column("sponsor_id", text),
        sa.Column("title_phase", text, nullable=False),
        sa.Column("intervention_types", sa.ARRAY(sa.Text), nullable=False),
    ]


def _run_id() -> sa.Column:
    return sa.Column("run_id", sa.BigInteger, sa.ForeignKey("monitoring.pipeline_runs.run_id"))


def _names_array() -> str:
    return "ARRAY[" + ", ".join(f"'{n}'" for n in FEATURE_NUMBERS) + "]::text[]"


def upgrade() -> None:
    # monitoring ---------------------------------------------------------------------------
    op.add_column("pipeline_runs", sa.Column("data_timestamp", sa.Date), schema="monitoring")
    op.add_column("pipeline_runs", sa.Column("run_kind", sa.Text), schema="monitoring")
    op.create_index(
        "one_running_job",
        "pipeline_runs",
        [sa.text("(status = 'running')")],
        unique=True,
        schema="monitoring",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_table("graded_predictions", schema="monitoring")
    op.create_table(
        "graded_predictions",
        sa.Column("prediction_date", sa.Date, nullable=False),
        sa.Column("horizon_months", sa.SmallInteger, nullable=False),
        sa.Column("landmark_index", sa.SmallInteger, nullable=False),  # -1: every index
        sa.Column("row_group", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("n_rows", sa.Integer, nullable=False),
        sa.Column("n_early_stop", sa.Integer, nullable=False),
        sa.Column("n_completed", sa.Integer, nullable=False),
        sa.Column("n_censored", sa.Integer, nullable=False),
        sa.Column("n_open", sa.Integer, nullable=False),
        sa.Column("mean_risk", sa.Double),
        sa.Column("auc", sa.Double),
        sa.Column("brier", sa.Double),
        sa.Column("calibration_slope", sa.Double),
        sa.Column("calibration_intercept", sa.Double),
        sa.Column("lift_top_10", sa.Double),
        sa.Column("matured", sa.Boolean, nullable=False),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint(
            "prediction_date", "horizon_months", "landmark_index", "row_group", "model_version"
        ),
        sa.CheckConstraint("row_group IN ('all', 'flagged')", name="graded_row_group"),
        schema="monitoring",
    )
    op.create_table(
        "score_files",
        sa.Column("month", sa.Date, primary_key=True),  # the first day of the month
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("rows", sa.Integer, nullable=False),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        _run_id(),
        sa.CheckConstraint("extract(day FROM month) = 1", name="score_file_month"),
        schema="monitoring",
    )

    # live ---------------------------------------------------------------------------------
    op.drop_table("trial_state", schema="live")
    op.drop_table("texts", schema="live")
    op.drop_table("study_versions", schema="live")
    op.create_table(
        "study_versions",
        sa.Column("nct_id", sa.Text, nullable=False),
        *_compact_fields(),
        sa.Column("feature_hash", sa.LargeBinary, nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        _run_id(),
        sa.PrimaryKeyConstraint("nct_id", "effective_date", "feature_hash"),
        schema="live",
    )
    op.create_index("study_versions_run", "study_versions", ["run_id"], schema="live")
    op.create_table(
        "trial_state",
        sa.Column("nct_id", sa.Text, primary_key=True),
        *_compact_fields(),
        sa.Column("feature_hash", sa.LargeBinary, nullable=False),
        sa.Column("first_start_date", sa.Date),
        sa.Column("first_primary_completion_date", sa.Date),
        sa.Column("first_completion_date", sa.Date),
        sa.Column("first_enrollment_count", sa.BigInteger),
        sa.Column("first_enrollment_type", sa.Text),
        sa.Column("n_versions", sa.Integer, nullable=False),
        sa.Column("ever_suspended", sa.Boolean, nullable=False),
        sa.Column("recent_post_dates", sa.ARRAY(sa.Date), nullable=False),
        sa.Column("lapse_from", sa.Date),
        sa.Column("reversal", sa.Boolean, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        _run_id(),
        schema="live",
    )
    op.create_table(
        "sponsor_file",
        sa.Column("file_id", sa.SmallInteger, primary_key=True),
        sa.Column("format", sa.Integer, nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("through_day", sa.Date, nullable=False),  # events of later days wait
        sa.Column("last_run_id", sa.BigInteger),  # versions of later runs are not consumed
        sa.Column("trials", sa.Integer, nullable=False),
        sa.Column("keys", sa.Integer, nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("file_id = 1", name="one_sponsor_file"),
        schema="live",
    )
    op.create_unique_constraint("quarantine_payload", "quarantine", ["payload_hash"], schema="live")
    op.add_column("outcome_events", sa.Column("kind", sa.Text), schema="live")
    op.execute("UPDATE live.outcome_events SET kind = 'status'")
    op.alter_column("outcome_events", "kind", nullable=False, schema="live")
    op.create_check_constraint(
        "outcome_kind", "outcome_events", "kind IN ('status', 'lapsed', 'reopened')",
        schema="live",
    )  # fmt: skip

    # serving ------------------------------------------------------------------------------
    names = _names_array()
    for table in ("trial_scores_latest", "trial_score_history", "trials_not_scored"):
        op.alter_column(
            table, "content_hash", type_=sa.LargeBinary, schema="serving",
            postgresql_using="decode(content_hash, 'hex')",
        )  # fmt: skip
    for name, kind in (
        ("driver_months", sa.ARRAY(sa.SmallInteger)),
        ("driver_features", sa.ARRAY(sa.SmallInteger)),
        ("driver_values", sa.ARRAY(sa.Text)),
        ("driver_contributions", sa.ARRAY(sa.REAL)),
    ):
        op.add_column("trial_scores_latest", sa.Column(name, kind), schema="serving")
    op.execute(
        f"""UPDATE serving.trial_scores_latest t SET
          driver_months = coalesce(d.m, '{{}}'), driver_features = coalesce(d.f, '{{}}'),
          driver_values = coalesce(d.v, '{{}}'), driver_contributions = coalesce(d.c, '{{}}')
        FROM (
          SELECT t2.nct_id,
            array_agg(h.key::smallint ORDER BY h.key::int, e.ord) AS m,
            array_agg((array_position({names}, e.item->>'feature') - 1)::smallint
                      ORDER BY h.key::int, e.ord) AS f,
            array_agg(e.item->>'value' ORDER BY h.key::int, e.ord) AS v,
            array_agg(round((e.item->>'contribution')::numeric, 3)::real
                      ORDER BY h.key::int, e.ord) AS c
          FROM serving.trial_scores_latest t2,
            jsonb_each(t2.drivers) h,
            jsonb_array_elements(h.value) WITH ORDINALITY e(item, ord)
          GROUP BY t2.nct_id
        ) d WHERE d.nct_id = t.nct_id"""
    )
    op.execute(
        """UPDATE serving.trial_scores_latest SET driver_months = '{}', driver_features = '{}',
          driver_values = '{}', driver_contributions = '{}' WHERE driver_months IS NULL"""
    )
    for name in ("driver_months", "driver_features", "driver_values", "driver_contributions"):
        op.alter_column("trial_scores_latest", name, nullable=False, schema="serving")
    op.create_check_constraint(
        "drivers_aligned",
        "trial_scores_latest",
        "cardinality(driver_months) = cardinality(driver_features) "
        "AND cardinality(driver_months) = cardinality(driver_values) "
        "AND cardinality(driver_months) = cardinality(driver_contributions) "
        "AND array_position(driver_features, NULL) IS NULL",
        schema="serving",
    )
    op.drop_column("trial_scores_latest", "drivers", schema="serving")
    op.drop_column("trial_scores_latest", "model_name", schema="serving")
    op.drop_column("trial_score_history", "model_name", schema="serving")
    op.add_column(
        "trial_score_history",
        sa.Column("run_id", sa.BigInteger, sa.ForeignKey("monitoring.pipeline_runs.run_id")),
        schema="serving",
    )
    op.create_table(
        "amendment_markers",
        sa.Column("nct_id", sa.Text, primary_key=True),
        sa.Column("post_dates", sa.ARRAY(sa.Date), nullable=False),
        sa.Column("kinds", sa.ARRAY(sa.SmallInteger), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("cardinality(post_dates) = cardinality(kinds)", name="markers_aligned"),
        schema="serving",
    )


def downgrade() -> None:
    names = _names_array()
    op.drop_table("amendment_markers", schema="serving")
    op.drop_column("trial_score_history", "run_id", schema="serving")
    for table in ("trial_scores_latest", "trial_score_history"):
        op.add_column(
            table, sa.Column("model_name", sa.Text, server_default="trialpulse-m4"),
            schema="serving",
        )  # fmt: skip
        op.alter_column(table, "model_name", nullable=False, server_default=None, schema="serving")
    op.add_column("trial_scores_latest", sa.Column("drivers", postgresql.JSONB), schema="serving")
    op.execute(
        f"""UPDATE serving.trial_scores_latest t SET drivers = coalesce((
          SELECT jsonb_object_agg(x.m, x.items) FROM (
            SELECT u.m::text AS m, jsonb_agg(jsonb_build_object(
                'feature', ({names})[u.f + 1], 'value', u.v, 'contribution', u.c,
                'direction', CASE WHEN u.c > 0 THEN 'raises' ELSE 'lowers' END)
              ORDER BY u.o) AS items
            FROM unnest(t.driver_months, t.driver_features, t.driver_values,
                        t.driver_contributions) WITH ORDINALITY u(m, f, v, c, o)
            GROUP BY u.m) x), '{{}}'::jsonb)"""
    )
    op.alter_column("trial_scores_latest", "drivers", nullable=False, schema="serving")
    op.drop_constraint("drivers_aligned", "trial_scores_latest", schema="serving", type_="check")
    for name in ("driver_months", "driver_features", "driver_values", "driver_contributions"):
        op.drop_column("trial_scores_latest", name, schema="serving")
    for table in ("trial_scores_latest", "trial_score_history", "trials_not_scored"):
        op.alter_column(
            table, "content_hash", type_=sa.Text, schema="serving",
            postgresql_using="encode(content_hash, 'hex')",
        )  # fmt: skip

    op.drop_constraint("outcome_kind", "outcome_events", schema="live", type_="check")
    op.execute("DELETE FROM live.outcome_events WHERE kind <> 'status'")
    op.drop_column("outcome_events", "kind", schema="live")
    op.drop_constraint("quarantine_payload", "quarantine", schema="live", type_="unique")
    op.drop_table("sponsor_file", schema="live")
    op.drop_table("trial_state", schema="live")
    op.drop_table("study_versions", schema="live")
    _recreate_0003_live_tables()

    op.drop_table("score_files", schema="monitoring")
    op.drop_table("graded_predictions", schema="monitoring")
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
    op.drop_index("one_running_job", "pipeline_runs", schema="monitoring")
    op.drop_column("pipeline_runs", "run_kind", schema="monitoring")
    op.drop_column("pipeline_runs", "data_timestamp", schema="monitoring")


def _recreate_0003_live_tables() -> None:
    """The live tables as revisions 0001 and 0002 left them (empty: their rows were API v2
    records of the old shape, and revision 0004 dropped them)."""
    text, date = sa.Text, sa.Date
    columns: list[sa.Column] = [
        sa.Column("nct_id", text, nullable=False),
        sa.Column("nct_version", sa.BigInteger),
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
        sa.Column("lead_sponsor_name", text),
        sa.Column("sponsor_key", text),
        sa.Column("sponsor_is_individual", sa.Boolean, nullable=False),
    ]
    for field in ("brief_title", "official_title", "brief_summary", "eligibility_criteria"):
        columns.append(sa.Column(f"{field}_hash", text))
    columns += [
        sa.Column("why_stopped_hash", text),
        sa.Column("content_hash", text, nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        _run_id(),
        sa.Column("last_known_status", text),
    ]
    op.create_table(
        "study_versions",
        *columns,
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
