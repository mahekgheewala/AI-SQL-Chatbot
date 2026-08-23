"""create chat_sessions table

Revision ID: e8b97a61149b
Revises: 3d4ba7631c35
Create Date: 2026-08-16 13:10:48.935630

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'e8b97a61149b'
down_revision: Union[str, Sequence[str], None] = '3d4ba7631c35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # chat_sessions is missing from the initial migration chain. Create it
    # idempotently so fresh deployments and existing databases both converge
    # on a schema that matches models/domain.py.
    if not inspect(op.get_bind()).has_table('chat_sessions'):
        op.create_table('chat_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_active_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_chat_sessions_id'), 'chat_sessions', ['id'], unique=False)
        op.create_index(op.f('ix_chat_sessions_session_id'), 'chat_sessions', ['session_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    if inspect(op.get_bind()).has_table('chat_sessions'):
        op.drop_index(op.f('ix_chat_sessions_session_id'), table_name='chat_sessions')
        op.drop_index(op.f('ix_chat_sessions_id'), table_name='chat_sessions')
        op.drop_table('chat_sessions')
