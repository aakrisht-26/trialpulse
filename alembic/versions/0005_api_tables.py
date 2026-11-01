"""The serving tables the API of Step 15 adds (ADRs 0046 to 0051).

- serving.data_status: one row, written by the daily job (the data-as-of date, the model
  version, the early stops recorded after the reason labels) and by the reason-count load
  (the date the labels run through).
- serving.trial_display: the brief title, keyed sponsor id, sponsor class and area codes of
  each live trial, from API v2 only.
- serving.sponsors: the display names of the sponsors that have one (ADR 0047), with the key
  the API matches a name against.
- serving.reason_counts: labeled early stops by month, reason and sponsor class (ADR 0050).
- serving.model_statement: the statement of ADR 0048.

No table holds a person's name: display names follow ADR 0044, and nothing else is a name.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "data_status",
        sa.Column("status_id", sa.SmallInteger, primary_key=True),
        sa.Column("data_as_of", sa.Date),
        sa.Column("model_version", sa.Text),
        sa.Column("labeled_through", sa.Date),
        sa.Column("early_stops_after_labels", sa.Integer),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("status_id = 1", name="one_status_row"),
        schema="serving",
    )
    op.create_table(
        "trial_display",
        sa.Column("nct_id", sa.Text, primary_key=True),
        sa.Column("brief_title", sa.Text),
        sa.Column("sponsor_id", sa.Text),
        sa.Column("sponsor_class", sa.Text),
        sa.Column("areas", sa.ARRAY(sa.SmallInteger), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema="serving",
    )
    op.create_index("trial_display_sponsor", "trial_display", ["sponsor_id"], schema="serving")
    op.create_index(
        "trial_display_areas", "trial_display", ["areas"], schema="serving",
        postgresql_using="gin",
    )  # fmt: skip
    op.create_table(
        "sponsors",
        sa.Column("sponsor_id", sa.Text, primary_key=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("search_key", sa.Text, nullable=False),
        sa.Column("sponsor_class", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema="serving",
    )
    op.create_index(
        "sponsors_search_key", "sponsors", ["search_key"], schema="serving",
        postgresql_ops={"search_key": "text_pattern_ops"},
    )  # fmt: skip
    op.create_table(
        "reason_counts",
        sa.Column("month", sa.Date, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("sponsor_class", sa.Text, nullable=False),
        sa.Column("n", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("month", "reason", "sponsor_class"),
        sa.CheckConstraint("n > 0", name="reason_count_positive"),
        schema="serving",
    )
    op.create_table(
        "model_statement",
        sa.Column("statement_id", sa.SmallInteger, primary_key=True),
        sa.Column("statement", postgresql.JSONB, nullable=False),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("statement_id = 1", name="one_statement"),
        schema="serving",
    )


def downgrade() -> None:
    for table in ("model_statement", "reason_counts", "sponsors", "trial_display", "data_status"):
        op.drop_table(table, schema="serving")
