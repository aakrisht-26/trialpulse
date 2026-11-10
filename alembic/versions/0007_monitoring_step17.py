"""Step 17's monitoring tables (ADRs 0076, 0077 and 0079).

- monitoring.graded_predictions gains `source` ('live' or 'replay', default 'live') in its
  primary key, so the replay's grades sit beside the live ones (ADR 0079).
- monitoring.drift: one row per column and week of the full run's drift step (ADR 0076).
- serving.monitoring_grades, serving.monitoring_drift and serving.monitoring_promotions:
  read-only views of the three monitoring tables, so the API's role keeps reading the
  `serving` schema only (ADR 0049); the API's role, where it exists, may select from them.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = ["prediction_date", "horizon_months", "landmark_index", "row_group", "model_version"]
VIEWS = ("monitoring_grades", "monitoring_drift", "monitoring_promotions")
API_ROLE = "trialpulse_api"


def upgrade() -> None:
    op.add_column(
        "graded_predictions",
        sa.Column("source", sa.Text, nullable=False, server_default="live"),
        schema="monitoring",
    )
    op.create_check_constraint(
        "graded_source", "graded_predictions", "source IN ('live', 'replay')", schema="monitoring"
    )
    op.drop_constraint(
        "graded_predictions_pkey", "graded_predictions", schema="monitoring", type_="primary"
    )
    op.create_primary_key(
        "graded_predictions_pkey", "graded_predictions", [*KEY, "source"], schema="monitoring"
    )
    op.create_table(
        "drift",
        sa.Column("week_date", sa.Date, nullable=False),  # the full run's data date
        sa.Column("column_name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("statistic", sa.Double),
        sa.Column("threshold", sa.Double, nullable=False),
        sa.Column("drifted", sa.Boolean, nullable=False),
        sa.Column("n_current", sa.Integer, nullable=False),
        sa.Column("run_id", sa.BigInteger),
        sa.Column(
            "written_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("week_date", "column_name"),
        sa.CheckConstraint("kind IN ('feature', 'score')", name="drift_kind"),
        schema="monitoring",
    )
    op.execute(
        """CREATE VIEW serving.monitoring_grades AS
        SELECT prediction_date, horizon_months, landmark_index, row_group, model_version,
               source, n_rows, n_early_stop, n_completed, n_censored, n_open, mean_risk, auc,
               brier, calibration_slope, calibration_intercept, lift_top_10, matured,
               graded_at
        FROM monitoring.graded_predictions"""
    )
    op.execute(
        """CREATE VIEW serving.monitoring_drift AS
        SELECT week_date, column_name, kind, method, statistic, threshold, drifted, n_current
        FROM monitoring.drift"""
    )
    op.execute(
        """CREATE VIEW serving.monitoring_promotions AS
        SELECT decided_at, champion_version, challenger_version, window_start, window_end,
               champion_auc_12m, challenger_auc_12m, challenger_calibration_slope, promoted,
               reason
        FROM monitoring.promotions"""
    )
    views = ", ".join(f"serving.{v}" for v in VIEWS)
    op.execute(
        f"""DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{API_ROLE}') THEN
            GRANT SELECT ON {views} TO {API_ROLE};
          END IF;
        END $$"""
    )


def downgrade() -> None:
    for view in VIEWS:
        op.execute(f"DROP VIEW IF EXISTS serving.{view}")
    op.drop_table("drift", schema="monitoring")
    op.drop_constraint(
        "graded_predictions_pkey", "graded_predictions", schema="monitoring", type_="primary"
    )
    op.execute("DELETE FROM monitoring.graded_predictions WHERE source <> 'live'")
    op.create_primary_key("graded_predictions_pkey", "graded_predictions", KEY,
                          schema="monitoring")  # fmt: skip
    op.drop_constraint("graded_source", "graded_predictions", schema="monitoring", type_="check")
    op.drop_column("graded_predictions", "source", schema="monitoring")
