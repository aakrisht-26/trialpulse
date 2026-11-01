"""The renewable lease of the live jobs (ADR 0060).

- monitoring.pipeline_runs.renewed_at: when a running job last renewed its lease. A running
  row is stale when its last renewal (not its start) is older than the stale window, so a
  long job that keeps renewing is never taken over, and a job whose row was taken over
  finds out at its next renewal and stops without writing.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "renewed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="monitoring",
    )
    op.execute("UPDATE monitoring.pipeline_runs SET renewed_at = started_at")


def downgrade() -> None:
    op.drop_column("pipeline_runs", "renewed_at", schema="monitoring")
