"""add name column to users

Revision ID: 9f1a2b6c7d3e
Revises: e8b97a61149b
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '9f1a2b6c7d3e'
down_revision: Union[str, Sequence[str], None] = 'e8b97a61149b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, no default — existing accounts simply have no display name
    # until they set one; nothing about login/auth depends on it being set.
    bind = op.get_bind()
    columns = [c["name"] for c in inspect(bind).get_columns("users")]
    if "name" not in columns:
        op.add_column('users', sa.Column('name', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    columns = [c["name"] for c in inspect(bind).get_columns("users")]
    if "name" in columns:
        op.drop_column('users', 'name')
