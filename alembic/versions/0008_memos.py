"""The per-trial risk memo (ADR 0084).

- serving.trial_memos: the latest memo of a trial that passed the fabrication check, with its
  payload (stored values only), payload hash, prompt version and model, and `valid_as_of`, the
  score date it was checked against; the API serves it only beside that score. The API's
  role, where it exists, may select from it (ADR 0049).
- monitoring.memo_runs: one row per memo run: counts, rejections by reason, tokens, and up to
  `memo.rejected_samples` rejected memos with their reasons for the review (never served).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

API_ROLE = "trialpulse_api"


def upgrade() -> None:
    op.create_table(
        "memo_runs",
        sa.Column("run_id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("score_date", sa.Date),
        sa.Column("n_chosen", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_current", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_called", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_stored", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_rejected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_deleted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_over_cap", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rejections", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "rejected_samples", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("stopped_reason", sa.Text),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'stopped', 'failed')", name="memo_run_status"
        ),
        schema="monitoring",
    )
    op.create_table(
        "trial_memos",
        sa.Column("nct_id", sa.Text, primary_key=True),
        sa.Column("payload_hash", sa.LargeBinary, nullable=False),
        sa.Column("payload_version", sa.SmallInteger, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("chosen_as", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("paragraphs", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("valid_as_of", sa.Date, nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("run_id", sa.BigInteger),
        sa.CheckConstraint("cardinality(paragraphs) = 3", name="memo_three_paragraphs"),
        schema="serving",
    )
    op.execute(
        f"""DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{API_ROLE}') THEN
            GRANT SELECT ON serving.trial_memos TO {API_ROLE};
          END IF;
        END $$"""
    )


def downgrade() -> None:
    op.drop_table("trial_memos", schema="serving")
    op.drop_table("memo_runs", schema="monitoring")
