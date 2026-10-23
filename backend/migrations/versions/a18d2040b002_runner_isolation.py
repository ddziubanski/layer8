"""Optional dedicated integration runners."""
from alembic import op
import sqlalchemy as sa

revision = "a18d2040b002"
down_revision = "a18d2040b001"
branch_labels = depends_on = None


def upgrade():
    op.add_column("runners", sa.Column("builtins_only", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade():
    op.drop_column("runners", "builtins_only")
