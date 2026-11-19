"""The inputs of the served sponsor rate on each score row, for the memo (ADR 0085, amended
2026-11-19).

- serving.trial_scores_latest gains `sponsor_has_record` (whether the sponsor has a track
  record of its own), `sponsor_other_ended` (the other ended trials behind the rate, NULL
  without a track record) and `sponsor_class_rate` (the class rate the sponsor's rate is
  smoothed toward, from the champion's priors). The daily job writes them with every score,
  so the memo can say exactly how the served rate was made. They are NULL on a row until its
  trial is next scored (every trial at the next full run). No other table changes; the API's
  role reads them through its existing grant on the table.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("sponsor_has_record", sa.Boolean()),
    ("sponsor_other_ended", sa.BigInteger()),
    ("sponsor_class_rate", postgresql.DOUBLE_PRECISION()),
)


def upgrade() -> None:
    for name, kind in COLUMNS:
        op.add_column("trial_scores_latest", sa.Column(name, kind, nullable=True),
                      schema="serving")  # fmt: skip


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("trial_scores_latest", name, schema="serving")
