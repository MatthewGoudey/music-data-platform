"""The `musicdata` command.

    musicdata ingest     # ListenBrainz → listen rows            (Phase 2)
    musicdata resolve    # listens → artists, release groups, tracklists (Phase 2)
    musicdata derive     # sessions and stats                    (Phase 2)
    musicdata shows      # Ticketmaster + do312 → shows          (Phase 3)
    musicdata dq         # acceptance checks → dq_result         (Phase 2)

A job whose phase has not landed yet is a no-op that still records a pipeline_run
row. `musicdata ingest --full` loads the whole ListenBrainz history.
`musicdata dq --fail` forces a failure to test the alert path.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from dotenv import load_dotenv

from musicdata import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)


@app.callback()
def _root(
    env: str | None = typer.Option(
        None,
        "--env",
        help="Load .env.<env> (e.g. dev, prod) in addition to .env. Sets MUSICDATA_ENV.",
    ),
    plain_logs: bool = typer.Option(
        False, "--plain-logs", help="Human-readable logs instead of JSON."
    ),
) -> None:
    load_dotenv(Path(".env"), override=False)
    if env:
        load_dotenv(Path(f".env.{env}"), override=True)
        os.environ["MUSICDATA_ENV"] = env
    from musicdata.log import configure_logging

    configure_logging(json_lines=not plain_logs)


def _trigger() -> str:
    """schedule (cron), dispatch (Actions run by hand), ci (any other Actions event), or manual."""
    event = os.environ.get("GITHUB_EVENT_NAME")
    if not event:
        return "manual"
    return {"schedule": "schedule", "workflow_dispatch": "dispatch"}.get(event, "ci")


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def ingest(
    since: str | None = typer.Option(
        None, help="ISO timestamp to start from (overrides the watermark)."
    ),
    full: bool = typer.Option(False, "--full", help="Page back through the whole history."),
) -> None:
    """Pull new listens from ListenBrainz and assign identity on the way in."""
    from datetime import UTC, datetime

    from musicdata.ingest.job import ingest as ingest_job
    from musicdata.jobs import runs

    start = datetime.fromisoformat(since) if since else None
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    raise typer.Exit(runs.run("ingest", ingest_job(full=full, since=start), trigger=_trigger()))


@app.command()
def resolve(
    limit: int = typer.Option(500, help="Max mapped release groups this run (1 req/s each)."),
    unmapped_limit: int = typer.Option(100, help="Max unmapped release groups this run."),
    min_listens: int = typer.Option(3, help="Skip unmapped groups with fewer listens."),
) -> None:
    """Fetch MusicBrainz metadata and canonical tracklists, most-listened groups first."""
    from musicdata.jobs import runs
    from musicdata.resolve.job import resolve as resolve_job

    raise typer.Exit(
        runs.run(
            "resolve",
            resolve_job(limit=limit, unmapped_limit=unmapped_limit, min_listens=min_listens),
            trigger=_trigger(),
        )
    )


@app.command()
def derive(
    full: bool = typer.Option(False, "--full", help="Rebuild every release group's sessions."),
) -> None:
    """Rebuild album sessions for changed release groups, then the stat tables."""
    from musicdata.derive.job import derive as derive_job
    from musicdata.jobs import runs

    raise typer.Exit(runs.run("derive", derive_job(full=full), trigger=_trigger()))


@app.command()
def shows() -> None:
    """Sync Chicago shows (Phase 3). No-op for now."""
    from musicdata.jobs import runs
    from musicdata.jobs.stubs import noop

    raise typer.Exit(runs.run("shows", noop({}), trigger=_trigger()))


@app.command()
def dq(
    fail: bool = typer.Option(False, "--fail", help="Force a failure to test alerting."),
) -> None:
    """Run the acceptance checks, store each result, and exit 1 when any fails."""
    from musicdata.jobs import runs
    from musicdata.jobs.dq import dq as dq_job
    from musicdata.jobs.stubs import forced_failure

    fn = forced_failure() if fail else dq_job()
    raise typer.Exit(runs.run("dq", fn, trigger=_trigger()))


if __name__ == "__main__":  # pragma: no cover
    app()
