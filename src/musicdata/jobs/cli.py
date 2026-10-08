"""The `musicdata` command.

    musicdata ingest     # ListenBrainz → listen rows            (Phase 2)
    musicdata resolve    # listens → artists, release groups, tracklists (Phase 2)
    musicdata derive     # sessions and stats                    (Phase 2)
    musicdata shows      # Ticketmaster + do312 → shows          (Phase 3)
    musicdata dq         # acceptance checks → dq_result         (Phase 2)

Phase 1 ships these as no-ops that still record a pipeline_run row, so the
scheduling, secrets, logging and alerting can be proven before any data moves.
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
) -> None:
    """Pull new listens from ListenBrainz (Phase 2). Currently a no-op that records a run."""
    from musicdata.jobs import runs
    from musicdata.jobs.stubs import noop

    raise typer.Exit(runs.run("ingest", noop({"since": since}), trigger=_trigger()))


@app.command()
def resolve(
    limit: int = typer.Option(500, help="Max MusicBrainz lookups this run (1 req/s)."),
) -> None:
    """Resolve listens to artists, release groups and tracklists (Phase 2). No-op for now."""
    from musicdata.jobs import runs
    from musicdata.jobs.stubs import noop

    raise typer.Exit(runs.run("resolve", noop({"limit": limit}), trigger=_trigger()))


@app.command()
def derive() -> None:
    """Recompute album sessions and stats (Phase 2). No-op for now."""
    from musicdata.jobs import runs
    from musicdata.jobs.stubs import noop

    raise typer.Exit(runs.run("derive", noop({}), trigger=_trigger()))


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
    """Run the acceptance checks and store results (Phase 2). No-op for now."""
    from musicdata.jobs import runs
    from musicdata.jobs.stubs import forced_failure, noop

    fn = forced_failure() if fail else noop({})
    raise typer.Exit(runs.run("dq", fn, trigger=_trigger()))


if __name__ == "__main__":  # pragma: no cover
    app()
