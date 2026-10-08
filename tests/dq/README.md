# Acceptance checks (Phase 2)

Each check is one SQL query with a threshold, run by `musicdata dq` after every sync,
and stored in `dq_result`. `/ops/status` shows the latest result per check.

The list of checks and thresholds lives in the plan (section "Acceptance checks"):
listen count vs ListenBrainz within 0.5%; no NULL `artist_id`; no duplicate keys;
≥ 90% of 10+-listen albums resolved; sessions never split across aliases; completion
overshoot ≤ 2%; show headliner residue 0; headliner resolution ≥ 95%; and so on.

Checks are plain Python functions returning `(observed, passed)`; see `musicdata/jobs/dq.py`
once it exists.
