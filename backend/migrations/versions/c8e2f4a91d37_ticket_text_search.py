# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket text search: full-text indexes on tickets and notes.

The expressions must match app/search.py exactly, or the indexes aren't used.

Revision ID: c8e2f4a91d37
Revises: b5d81e2c4a90
"""
from alembic import op

revision = "c8e2f4a91d37"
down_revision = "b5d81e2c4a90"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX ix_tickets_text ON tickets USING gin (to_tsvector('simple'::regconfig, (coalesce(subject, '') || ' ') || coalesce(description, '')))")
    op.execute("CREATE INDEX ix_comments_text ON comments USING gin (to_tsvector('simple'::regconfig, body))")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_comments_text")
    op.execute("DROP INDEX IF EXISTS ix_tickets_text")
