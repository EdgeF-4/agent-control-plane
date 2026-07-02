"""initial schema

The unified projection store: tenants, users, projects, runs, the hash-chained
run-event trail, policy decisions, and eval-run history. This matches the phase-1
models exactly; from here the schema evolves through numbered migrations rather
than ``create_all``.

Revision ID: 0001
Revises:
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('eval_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=True),
    sa.Column('eval_run_id', sa.String(length=128), nullable=False),
    sa.Column('suite_name', sa.String(length=200), nullable=False),
    sa.Column('suite_hash', sa.String(length=64), nullable=False),
    sa.Column('pass_rate', sa.Float(), nullable=False),
    sa.Column('mean_score', sa.Float(), nullable=False),
    sa.Column('total_cost_usd', sa.Float(), nullable=False),
    sa.Column('passed', sa.Integer(), nullable=False),
    sa.Column('failed', sa.Integer(), nullable=False),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('is_baseline', sa.Boolean(), nullable=False),
    sa.Column('has_regressions', sa.Boolean(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('eval_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_eval_runs_eval_run_id'), ['eval_run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_eval_runs_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_eval_runs_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('tenants',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tenants_slug'), ['slug'], unique=True)

    op.create_table('projects',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('budget_limit_micro', sa.BigInteger(), nullable=False),
    sa.Column('budget_period', sa.String(length=16), nullable=False),
    sa.Column('budget_warn_threshold', sa.Float(), nullable=False),
    sa.Column('budget_latches_kill', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'slug', name='uq_project_slug')
    )
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_projects_slug'), ['slug'], unique=False)
        batch_op.create_index(batch_op.f('ix_projects_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=200), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'email', name='uq_user_email')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('external_run_id', sa.String(length=128), nullable=False),
    sa.Column('agent_name', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('total_input_tokens', sa.BigInteger(), nullable=False),
    sa.Column('total_output_tokens', sa.BigInteger(), nullable=False),
    sa.Column('total_cost_micro', sa.BigInteger(), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_runs_external_run_id'), ['external_run_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_runs_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_runs_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_runs_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('policy_decisions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=True),
    sa.Column('server', sa.String(length=128), nullable=False),
    sa.Column('tool', sa.String(length=200), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('policy_decisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_policy_decisions_decision'), ['decision'], unique=False)
        batch_op.create_index(batch_op.f('ix_policy_decisions_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_policy_decisions_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('run_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=True),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('cost_micro', sa.BigInteger(), nullable=True),
    sa.Column('tokens', sa.JSON(), nullable=True),
    sa.Column('latency_ms', sa.Float(), nullable=True),
    sa.Column('payload_summary', sa.JSON(), nullable=False),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.Column('prev_hash', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'seq', name='uq_event_seq')
    )
    with op.batch_alter_table('run_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_events_event_type'), ['event_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_run_events_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_run_events_tenant_id'), ['tenant_id'], unique=False)



def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('run_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_run_events_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_run_events_run_id'))
        batch_op.drop_index(batch_op.f('ix_run_events_event_type'))

    op.drop_table('run_events')
    with op.batch_alter_table('policy_decisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_policy_decisions_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_policy_decisions_run_id'))
        batch_op.drop_index(batch_op.f('ix_policy_decisions_decision'))

    op.drop_table('policy_decisions')
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_runs_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_runs_status'))
        batch_op.drop_index(batch_op.f('ix_runs_project_id'))
        batch_op.drop_index(batch_op.f('ix_runs_external_run_id'))

    op.drop_table('runs')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_projects_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_projects_slug'))

    op.drop_table('projects')
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tenants_slug'))

    op.drop_table('tenants')
    with op.batch_alter_table('eval_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_eval_runs_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_eval_runs_project_id'))
        batch_op.drop_index(batch_op.f('ix_eval_runs_eval_run_id'))

    op.drop_table('eval_runs')
