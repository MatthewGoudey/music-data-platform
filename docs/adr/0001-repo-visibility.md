# ADR 0001: Repository visibility

- Status: Accepted (recommendation; change by editing this file)
- Date: 2026-10-07
- Decision: Public

Public repository on GitHub.

The repository holds code, migrations, seed lists and workflows. Listening data lives in Neon, secrets in GitHub Environments and Fly, and gitleaks runs in pre-commit. A public repo gets unlimited Actions minutes and doubles as portfolio work. GitHub disables scheduled workflows on public repos after 60 days without a commit; the nightly sync commits a small status file monthly to prevent that.

Alternative: private (2,000 Actions minutes a month, no auto-disable).
