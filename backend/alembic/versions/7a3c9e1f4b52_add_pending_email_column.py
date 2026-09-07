"""add pending_email column to users

Revision ID: 7a3c9e1f4b52
Revises: 9f1a2b6c7d3e
Create Date: 2026-09-08 00:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '7a3c9e1f4b52'
down_revision: Union[str, Sequence[str], None] = '9f1a2b6c7d3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Holds the requested new email address while it awaits confirmation —
    # the actual `email` column only changes once the new address is
    # verified (reuses the existing verification_token_hash/expires_at
    # columns rather than adding a second token pair).
    bind = op.get_bind()
    columns = [c["name"] for c in inspect(bind).get_columns("users")]
    if "pending_email" not in columns:
        op.add_column('users', sa.Column('pending_email', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    columns = [c["name"] for c in inspect(bind).get_columns("users")]
    if "pending_email" in columns:
        op.drop_column('users', 'pending_email')
