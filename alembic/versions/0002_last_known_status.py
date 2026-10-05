"""Add last_known_status to live.study_versions (ADR 0014).

The canonical version contract gained last_known_status: the submitted status behind the
registry's computed UNKNOWN (API v2 statusModule.lastKnownStatus). Step 4 needs it to apply
the registry's UNKNOWN rule to versioned fields.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("study_versions", sa.Column("last_known_status", sa.Text), schema="live")


def downgrade() -> None:
    op.drop_column("study_versions", "last_known_status", schema="live")
