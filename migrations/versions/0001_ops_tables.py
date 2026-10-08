"""ops tables: pipeline_run and dq_result

Revision ID: 0001
Revises:
Create Date: 2026-10-07
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pipeline_run (
            id            BIGSERIAL PRIMARY KEY,
            job           TEXT        NOT NULL,
            env           TEXT        NOT NULL,
            trigger       TEXT        NOT NULL DEFAULT 'manual',
            started_at    TIMESTAMPTZ NOT NULL,
            finished_at   TIMESTAMPTZ,
            status        TEXT        NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
            rows_affected INTEGER     NOT NULL DEFAULT 0,
            error         TEXT,
            notes         JSONB       NOT NULL DEFAULT '{}'::jsonb
        );
        CREATE INDEX pipeline_run_job_started_idx ON pipeline_run (job, started_at DESC);

        CREATE TABLE dq_result (
            id          BIGSERIAL PRIMARY KEY,
            run_id      BIGINT REFERENCES pipeline_run (id) ON DELETE SET NULL,
            check_name  TEXT        NOT NULL,
            passed      BOOLEAN     NOT NULL,
            observed    NUMERIC,
            threshold   TEXT,
            details     JSONB       NOT NULL DEFAULT '{}'::jsonb,
            checked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX dq_result_check_checked_idx ON dq_result (check_name, checked_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dq_result; DROP TABLE IF EXISTS pipeline_run;")
