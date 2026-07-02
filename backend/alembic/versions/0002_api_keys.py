"""api keys

Per-project ingest credentials (SHA-256 digest + short prefix only) so agents
and SDKs can POST runs without the admin token.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('api_keys',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('key_prefix', sa.String(length=16), nullable=False),
    sa.Column('key_sha256', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_api_keys_key_prefix'), ['key_prefix'], unique=False)
        batch_op.create_index(batch_op.f('ix_api_keys_key_sha256'), ['key_sha256'], unique=True)
        batch_op.create_index(batch_op.f('ix_api_keys_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_api_keys_tenant_id'), ['tenant_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_keys_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_api_keys_project_id'))
        batch_op.drop_index(batch_op.f('ix_api_keys_key_sha256'))
        batch_op.drop_index(batch_op.f('ix_api_keys_key_prefix'))

    op.drop_table('api_keys')
