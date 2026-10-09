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
    rekey: bool = typer.Option(
        False, "--rekey", help="Re-apply identity rules to stored listens (whole history)."
    ),
) -> None:
    """Pull new listens from ListenBrainz and assign identity on the way in."""
    from datetime import UTC, datetime

    from musicdata.ingest.job import ingest as ingest_job
    from musicdata.jobs import runs

    start = datetime.fromisoformat(since) if since else None
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    raise typer.Exit(
        runs.run("ingest", ingest_job(full=full, since=start, rekey=rekey), trigger=_trigger())
    )


@app.command()
def resolve(
    limit: int = typer.Option(500, help="Max mapped release groups this run (1 req/s each)."),
    unmapped_limit: int = typer.Option(100, help="Max unmapped release groups this run."),
    min_listens: int = typer.Option(3, help="Skip unmapped groups with fewer listens."),
    retry_unresolved: bool = typer.Option(
        False, "--retry-unresolved", help="Retry unmapped groups marked unresolved now."
    ),
) -> None:
    """Fetch MusicBrainz metadata and canonical tracklists, most-listened groups first."""
    from musicdata.jobs import runs
    from musicdata.resolve.job import resolve as resolve_job

    raise typer.Exit(
        runs.run(
            "resolve",
            resolve_job(
                limit=limit,
                unmapped_limit=unmapped_limit,
                min_listens=min_listens,
                retry_unresolved=retry_unresolved,
            ),
            trigger=_trigger(),
        )
    )


@app.command("recheck-tracklists")
def recheck_tracklists_cmd(
    limit: int = typer.Option(500, help="Max albums to ask MusicBrainz about again."),
) -> None:
    """Re-resolve albums whose tracklist looks cut short (many played titles match no track)."""
    from musicdata.jobs import runs
    from musicdata.resolve.recheck import recheck_tracklists

    raise typer.Exit(
        runs.run("recheck_tracklists", recheck_tracklists(limit=limit), trigger=_trigger())
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
def shows(
    sweep: bool = typer.Option(False, "--sweep", help="Read every venue, not just active ones."),
    venue_limit: int | None = typer.Option(None, help="Read at most this many venues (tests)."),
) -> None:
    """Sync Chicago shows from Oh My Rockness and resolve their performers to artists."""
    from musicdata.jobs import runs
    from musicdata.shows.job import shows as shows_job

    raise typer.Exit(
        runs.run("shows", shows_job(sweep=sweep, venue_limit=venue_limit), trigger=_trigger())
    )


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


graph_app = typer.Typer(no_args_is_help=True, help="The music graph (docs/graph/GRAPH_SPEC.md).")
app.add_typer(graph_app, name="graph")


@graph_app.command("seed")
def graph_seed_cmd(
    root: str = typer.Option(".", help="Repository root holding seeds/graph/."),
) -> None:
    """Load predicates, the v_atlas map, its lanes and lane_parent claims, the lists, and an
    album entity with map coordinates for every resolved atlas entry. Idempotent."""
    from musicdata.graph.seed import graph_seed
    from musicdata.jobs import runs

    raise typer.Exit(runs.run("graph_seed", graph_seed(Path(root)), trigger=_trigger()))


@graph_app.command("import")
def graph_import_cmd(
    slice_: str = typer.Option(
        "crazy_horse", "--slice", help="crazy_horse, v_atlas or album:<id>."
    ),
    limit: int | None = typer.Option(None, help="Max albums this run."),
    refresh: bool = typer.Option(False, "--refresh", help="Baseline albums already done again."),
) -> None:
    """The free baseline (MusicBrainz, Wikidata, Discogs) for a slice's albums. Resumable."""
    from musicdata.graph.importer import graph_import
    from musicdata.jobs import runs

    raise typer.Exit(
        runs.run(
            "graph_import",
            graph_import(slice_name=slice_, limit=limit, refresh=refresh),
            trigger=_trigger(),
        )
    )


@graph_app.command("link")
def graph_link_cmd() -> None:
    """Point album and artist entities at release_group and artist rows by MBID."""
    from musicdata.graph.importer import graph_link
    from musicdata.jobs import runs

    raise typer.Exit(runs.run("graph_link", graph_link(), trigger=_trigger()))


@graph_app.command("report")
def graph_report_cmd(
    slice_: str = typer.Option("crazy_horse", "--slice", help="The slice to report on."),
) -> None:
    """Coverage per facet, claims by predicate and source, gaps and no-match albums."""
    from musicdata.graph.report import graph_report
    from musicdata.jobs import runs

    raise typer.Exit(runs.run("graph_report", graph_report(slice_name=slice_), trigger=_trigger()))


venues_app = typer.Typer(no_args_is_help=True, help="Venues (shows).")
app.add_typer(venues_app, name="venues")


@venues_app.command("travel")
def venues_travel_cmd(
    limit: int = typer.Option(1000, help="Max venues this run."),
    refresh: bool = typer.Option(False, "--refresh", help="Re-time venues that have times."),
) -> None:
    """Minutes by CTA and on foot from HOME_ADDRESS to venues without times (Google Routes)."""
    from musicdata.jobs import runs
    from musicdata.shows.travel import venues_travel

    raise typer.Exit(
        runs.run("venues_travel", venues_travel(limit=limit, refresh=refresh), trigger=_trigger())
    )


lists_app = typer.Typer(no_args_is_help=True, help="Lists and the atlas (docs/QUEUE_SPEC.md).")
app.add_typer(lists_app, name="lists")


@lists_app.command("load")
def lists_load(
    root: str = typer.Option(".", help="Repository root holding seeds/."),
) -> None:
    """Load seeds/lists/_lists.csv, every list it names, and the atlas lanes and paths.
    Idempotent; run from the laptop (the third-party lists are not on GitHub)."""
    from musicdata.jobs import runs
    from musicdata.lists.load import lists_load as load_job

    raise typer.Exit(runs.run("lists", load_job(root=Path(root)), trigger=_trigger()))


@lists_app.command("copy-resolutions")
def lists_copy_resolutions(
    source: str = typer.Option(..., help="Environment to copy from, e.g. dev (.env.<source>)."),
) -> None:
    """Settle pending entries from another environment's matches (by MusicBrainz ID).
    Run from the laptop: `musicdata --env prod lists copy-resolutions --source dev`."""
    from dotenv import dotenv_values

    from musicdata.jobs import runs
    from musicdata.lists.copy import lists_copy

    url = dotenv_values(Path(f".env.{source}")).get("DATABASE_URL")
    if not url:
        raise typer.BadParameter(f"no DATABASE_URL in .env.{source}")
    raise typer.Exit(runs.run("lists_copy", lists_copy(url), trigger=_trigger()))


@lists_app.command("reject-compilations")
def lists_reject_compilations(
    list_: str = typer.Option(..., "--list", help="List slug, e.g. billboard_200."),
) -> None:
    """Set aside a list's entries that resolved to a compilation (never heard as an album)."""
    from musicdata.jobs import runs
    from musicdata.lists.compilations import reject_compilations

    raise typer.Exit(runs.run("lists_reject", reject_compilations(list_), trigger=_trigger()))


@lists_app.command("resolve")
def lists_resolve(
    limit: int = typer.Option(300, help="Max MusicBrainz searches this run (1 req/s each)."),
    retry: bool = typer.Option(False, "--retry", help="Search unresolved entries again."),
) -> None:
    """Match pending list entries to release groups: known albums first, then MusicBrainz."""
    from musicdata.jobs import runs
    from musicdata.lists.resolve import lists_resolve as resolve_job

    raise typer.Exit(
        runs.run("lists_resolve", resolve_job(limit=limit, retry=retry), trigger=_trigger())
    )


if __name__ == "__main__":  # pragma: no cover
    app()
