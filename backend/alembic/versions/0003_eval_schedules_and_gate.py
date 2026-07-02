"""eval schedules and gate

Recurring reliability runs (eval_schedules) and a per-project eval gate that
blocks new runs while a suite is regressed (projects.eval_gate_suite).

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('eval_schedules',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=True),
    sa.Column('suite_name', sa.String(length=200), nullable=False),
    sa.Column('interval_minutes', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('eval_schedules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eval_schedules_next_run_at'), ['next_run_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_eval_schedules_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_eval_schedules_tenant_id'), ['tenant_id'], unique=False)

    # Backfill existing rows with the empty (no-gate) default.
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('eval_gate_suite', sa.String(length=200), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('eval_gate_suite')

    with op.batch_alter_table('eval_schedules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eval_schedules_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_eval_schedules_project_id'))
        batch_op.drop_index(batch_op.f('ix_eval_schedules_next_run_at'))

    op.drop_table('eval_schedules')
