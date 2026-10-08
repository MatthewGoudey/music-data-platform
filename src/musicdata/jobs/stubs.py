"""Phase 1 job bodies: do nothing, prove the plumbing."""

from __future__ import annotations

from musicdata.jobs.runs import JobFn, RunContext


def noop(params: dict[str, object]) -> JobFn:
    async def _run(ctx: RunContext) -> None:
        ctx.notes["phase"] = 1
        ctx.notes["params"] = params
        ctx.notes["message"] = "no-op: job body lands in a later phase"
        ctx.rows = 0

    return _run


def forced_failure() -> JobFn:
    async def _run(ctx: RunContext) -> None:
        ctx.notes["message"] = "forced failure requested with --fail"
        raise RuntimeError("forced failure to test the alert path")

    return _run
