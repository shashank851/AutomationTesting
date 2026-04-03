"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── test_runs ─────────────────────────────────────────────────────────────
    op.create_table(
        "test_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at",  sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_url",       sa.String(2048),  nullable=False),
        sa.Column("input_filename",   sa.String(512),   nullable=False),
        sa.Column("input_file_path",  sa.String(1024),  nullable=False),
        sa.Column("output_file_path", sa.String(1024),  nullable=True),
        sa.Column("requires_auth",    sa.Boolean(),     nullable=False, server_default="false"),
        sa.Column("auth_state_path",  sa.String(1024),  nullable=True),
        sa.Column("status",           sa.String(20),    nullable=False, server_default="pending"),
        sa.Column("celery_task_id",   sa.String(255),   nullable=True),
        sa.Column("error_message",    sa.Text(),        nullable=True),
        sa.Column("total_tests",      sa.Integer(),     nullable=True),
        sa.Column("passed_count",     sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("failed_count",     sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("manual_count",     sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("skipped_count",    sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("started_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at",     sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_test_runs_status",          "test_runs", ["status"])
    op.create_index("ix_test_runs_celery_task_id",  "test_runs", ["celery_task_id"])

    # ── test_results ──────────────────────────────────────────────────────────
    op.create_table(
        "test_results",
        sa.Column("id",           sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id",       postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_case_id", sa.String(50),  nullable=False),
        sa.Column("title",        sa.Text(),      nullable=True),
        sa.Column("status",       sa.String(20),  nullable=False),
        sa.Column("actual_result",sa.Text(),      nullable=True),
        sa.Column("comments",     sa.Text(),      nullable=True),
        sa.Column("error_detail", sa.Text(),      nullable=True),
        sa.Column("requires_auth",sa.Boolean(),   nullable=False, server_default="false"),
        sa.Column("executed_at",  sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "test_case_id", name="uq_test_results_run_test"),
    )
    op.create_index("ix_test_results_run_id", "test_results", ["run_id"])

    # ── run_events ────────────────────────────────────────────────────────────
    op.create_table(
        "run_events",
        sa.Column("id",         sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(50),   nullable=False),
        sa.Column("payload",    postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])


def downgrade() -> None:
    op.drop_table("run_events")
    op.drop_table("test_results")
    op.drop_table("test_runs")
