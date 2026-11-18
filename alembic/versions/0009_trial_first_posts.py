"""The first-post date of each live trial, for the API's timeline (ADR 0043, amended
2026-11-18).

- serving.trial_first_posts: a read-only view of `live.trial_state`'s registry id and
  first-post date, so the API names a trial's first posting as such (`trialpulse.markers`)
  while its role keeps reading the `serving` schema only (ADR 0049, as ADR 0079's views do).
  The API's role, where it exists, may select from it. No table or row changes.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

API_ROLE = "trialpulse_api"


def upgrade() -> None:
    op.execute(
        """CREATE VIEW serving.trial_first_posts AS
        SELECT nct_id, study_first_post_date AS first_post_date
        FROM live.trial_state"""
    )
    op.execute(
        f"""DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{API_ROLE}') THEN
            GRANT SELECT ON serving.trial_first_posts TO {API_ROLE};
          END IF;
        END $$"""
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS serving.trial_first_posts")
