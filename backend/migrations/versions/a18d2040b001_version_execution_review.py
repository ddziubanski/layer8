"""Save interpreter and first administrator review with each script version."""
from alembic import op
import sqlalchemy as sa

revision = "a18d2040b001"
down_revision = "8cbdccc1f8f8"
branch_labels = depends_on = None


def upgrade():
    op.add_column("script_versions", sa.Column("language", sa.String(32), nullable=True))
    op.add_column("script_versions", sa.Column("reviewed_by_id", sa.Integer(), nullable=True))
    op.add_column("script_versions", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_script_versions_reviewed_by_id_users", "script_versions", "users", ["reviewed_by_id"], ["id"], ondelete="SET NULL")
    op.create_check_constraint("ck_script_versions_language", "script_versions", "language IN ('sql', 'powershell', 'bash', 'python', 'eos')")
    op.execute("UPDATE script_versions v SET language = s.language FROM scripts s WHERE s.id = v.script_id")
    # Historical interpreter changes were not recorded; do not run queued work using a guess.
    # Do not invent administrator review provenance for pre-existing code.
    op.execute("UPDATE script_runs SET status = 'cancelled', sealed = NULL, finished_at = now(), error = 'Cancelled during version execution hardening; request a new run' WHERE status IN ('queued', 'awaiting_approval')")


def downgrade():
    op.drop_constraint("ck_script_versions_language", "script_versions", type_="check")
    op.drop_constraint("fk_script_versions_reviewed_by_id_users", "script_versions", type_="foreignkey")
    op.drop_column("script_versions", "reviewed_at")
    op.drop_column("script_versions", "reviewed_by_id")
    op.drop_column("script_versions", "language")
