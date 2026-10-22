"""portal-only sessions (single sign-on)

Revision ID: b7c1d2e3f4a5
Revises: 4ed41e220ce6
Create Date: 2026-10-18 22:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = '4ed41e220ce6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('user_sessions', sa.Column('portal_only', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('user_sessions', 'portal_only')
