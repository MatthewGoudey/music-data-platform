"""Jobs: the five CLI commands GitHub Actions runs (ingest, resolve, derive, shows, dq).

Every job runs inside `runs.record()` so it leaves a pipeline_run row whether it
succeeds or fails. /ops/status reads those rows.
"""
