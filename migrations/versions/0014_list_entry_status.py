"""list_entry_status: heard / started / unheard for every accepted, resolved entry

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-09

docs/QUEUE_SPEC.md section 6. Heard is computed from album_session at read time: heard
means at least one full session, started at least one partial session and no full one,
unheard neither. Every progress number and every queue exclusion reads this view.
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW list_entry_status AS
        SELECT e.entry_id, e.list_id, e.release_group_id,
               CASE WHEN s.full_sessions > 0 THEN 'heard'
                    WHEN s.partial_sessions > 0 THEN 'started'
                    ELSE 'unheard' END AS status,
               s.full_sessions, s.partial_sessions,
               coalesce(rs.listens, 0) AS listens,
               rs.tracks_heard,
               coalesce(rs.track_count, t.track_count) AS track_count,
               s.last_session_at,
               q.pinned_at IS NOT NULL AS pinned,
               q.snoozed_until,
               q.hidden_at IS NOT NULL AS hidden
          FROM list_entry e
          CROSS JOIN LATERAL (
                SELECT count(*) FILTER (WHERE a.session_type = 'full') AS full_sessions,
                       count(*) FILTER (WHERE a.session_type = 'partial') AS partial_sessions,
                       max(a.started_at) AS last_session_at
                  FROM album_session a
                 WHERE a.release_group_id = e.release_group_id) s
          LEFT JOIN release_group_stat rs ON rs.release_group_id = e.release_group_id
          LEFT JOIN release_group_tracklist t ON t.release_group_id = e.release_group_id
          LEFT JOIN queue_state q ON q.release_group_id = e.release_group_id
         WHERE e.review_status = 'accepted'
           AND e.resolve_status = 'resolved'
           AND e.release_group_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS list_entry_status;")
