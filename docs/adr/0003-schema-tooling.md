# ADR 0003: Schema migrations

- Status: Accepted (recommendation; change by editing this file)
- Date: 2026-10-07
- Decision: Alembic with plain SQL

Alembic, with each revision written as plain SQL in `op.execute`. No SQLAlchemy models, no autogenerate. The schema stays readable, reviewable, and identical in CI, dev and prod; `DATABASE_URL` is the only input.

Alternatives: yoyo-migrations or dbmate (SQL-only, fewer dependencies); SQLAlchemy ORM (not needed for raw-SQL jobs).
