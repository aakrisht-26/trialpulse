"""The served scores of Step 13 (ADR 0034).

- serving.trial_scores_latest gains the true landmark index and the flag "outside the
  validated range"; its drivers hold the five drivers of each horizon.
- serving.trial_score_history records why each row was written: one or more of
  first_score, record_change, landmark_passed, new_model and calendar.
- serving.trials_not_scored: the open trials that get no score, with their reason code.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")
REASONS = ("first_score", "record_change", "landmark_passed", "new_model", "calendar")
NOT_SCORED = ("reversal", "beyond_120_months", "band_not_validated")


def _array(values: Sequence[str]) -> str:
    return "ARRAY[" + ", ".join(f"'{v}'" for v in values) + "]::text[]"


def upgrade() -> None:
    op.add_column(
        "trial_scores_latest", sa.Column("true_landmark_index", sa.SmallInteger), schema="serving"
    )
    op.add_column(
        "trial_scores_latest",
        sa.Column("flagged", sa.Boolean, nullable=False, server_default=sa.false()),
        schema="serving",
    )
    op.drop_constraint("history_reason", "trial_score_history", schema="serving", type_="check")
    op.drop_column("trial_score_history", "reason", schema="serving")
    op.add_column(
        "trial_score_history",
        sa.Column("reasons", sa.ARRAY(sa.Text), nullable=False),
        schema="serving",
    )
    for name, kind in (
        ("landmark_index", sa.SmallInteger),
        ("true_landmark_index", sa.SmallInteger),
        ("content_hash", sa.Text),
    ):
        op.add_column("trial_score_history", sa.Column(name, kind), schema="serving")
    op.add_column(
        "trial_score_history",
        sa.Column("flagged", sa.Boolean, nullable=False, server_default=sa.false()),
        schema="serving",
    )
    op.create_check_constraint(
        "history_reasons",
        "trial_score_history",
        f"cardinality(reasons) > 0 AND reasons <@ {_array(REASONS)}",
        schema="serving",
    )
    op.create_table(
        "trials_not_scored",
        sa.Column("nct_id", sa.Text, primary_key=True),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("true_landmark_index", sa.SmallInteger),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("content_hash", sa.Text),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "reason IN (" + ", ".join(f"'{r}'" for r in NOT_SCORED) + ")", name="not_scored_reason"
        ),
        schema="serving",
    )


def downgrade() -> None:
    op.drop_table("trials_not_scored", schema="serving")
    op.drop_constraint("history_reasons", "trial_score_history", schema="serving", type_="check")
    for name in ("flagged", "content_hash", "true_landmark_index", "landmark_index", "reasons"):
        op.drop_column("trial_score_history", name, schema="serving")
    op.add_column(
        "trial_score_history",
        sa.Column("reason", sa.Text, nullable=False, server_default="first_score"),
        schema="serving",
    )
    op.create_check_constraint(
        "history_reason",
        "trial_score_history",
        "reason IN ('first_score', 'score_moved', 'record_changed')",
        schema="serving",
    )
    op.drop_column("trial_scores_latest", "flagged", schema="serving")
    op.drop_column("trial_scores_latest", "true_landmark_index", schema="serving")
