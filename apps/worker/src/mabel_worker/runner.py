"""The worker loop.

Claim a batch, run each job, mark it done or push it back. Nothing clever.

Two behaviours worth naming.

**One job failing never stops the loop.** A recap that raises must not prevent
the next tenant's recap. Each job is wrapped, and a failure becomes a retry
with backoff rather than a crash.

**Shutdown drains rather than drops.** Fly sends SIGTERM on a deploy. A worker
that exits immediately leaves claimed jobs locked until their lease expires
five minutes later, which for a 7am recap means it arrives at 7:05 or not at
all. So we stop claiming and finish what we hold.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.tenant import dispose_engine, get_engine
from mabel_worker import queue
from mabel_worker.queue import Job

logger = logging.getLogger(__name__)

# How long to wait when there was nothing to do. Short enough that a 7am recap
# is not noticeably late, long enough not to hammer the database all night.
IDLE_SLEEP_SECONDS = 5.0

Handler = Callable[[Job, AsyncEngine], Awaitable[None]]


class UnknownJobKind(RuntimeError):
    """A job kind nothing handles. Usually a cron entry added without a
    handler, which would otherwise retry silently until it gave up."""


def build_registry() -> dict[str, Handler]:
    """Job kind -> handler.

    Imported inside the function so a broken handler module fails when the
    worker starts, with a traceback naming it, rather than at import time in
    something unrelated that happened to import this.
    """
    from mabel_worker.jobs import (
        followup_nudge,
        monthly_report,
        morning_recap,
        purge_recording,
        qa_review,
        send_notification,
        silence_alert,
        weekly_summary,
    )

    return {
        "morning_recap": morning_recap.run,
        "weekly_summary": weekly_summary.run,
        "followup_nudge": followup_nudge.run,
        "silence_alert": silence_alert.run,
        "monthly_report": monthly_report.run,
        "purge_recording": purge_recording.run,
        "qa_review": qa_review.run,
        "send_notification": send_notification.run,
    }


async def run_one(job: Job, engine: AsyncEngine, registry: dict[str, Handler]) -> None:
    """Run a single job and settle it. Never raises."""
    handler = registry.get(job.kind)
    if handler is None:
        # Not retried. Retrying a kind nothing handles just burns the attempts
        # and hides the real problem, which is a missing handler.
        await queue.fail(engine, job.id, f"no handler for job kind {job.kind!r}")
        return

    try:
        await handler(job, engine)
    except Exception as exc:  # noqa: BLE001 - one bad job must not stop the loop
        logger.exception("job %s (%s) failed", job.id, job.kind)
        await queue.retry_later(engine, job, f"{type(exc).__name__}: {exc}")
        return

    await queue.complete(engine, job.id)


async def run_batch(engine: AsyncEngine, registry: dict[str, Handler]) -> int:
    """One pass. Returns how many jobs ran, so the loop knows whether to sleep."""
    jobs = await queue.claim(engine)
    if not jobs:
        return 0

    # Sequential on purpose. These are small jobs against one database, and
    # running them concurrently would mean several tenant_scope transactions
    # open at once on a small pool for no real gain.
    for job in jobs:
        await run_one(job, engine, registry)
    return len(jobs)


class Runner:
    def __init__(self, engine: AsyncEngine, registry: dict[str, Handler] | None = None):
        self.engine = engine
        self.registry = registry if registry is not None else build_registry()
        self._stopping = asyncio.Event()

    def request_stop(self) -> None:
        """Stop claiming. Whatever is in flight finishes."""
        logger.info("worker shutting down; finishing the current batch")
        self._stopping.set()

    async def run_forever(self) -> None:
        while not self._stopping.is_set():
            try:
                ran = await run_batch(self.engine, self.registry)
            except Exception:  # noqa: BLE001 - the database went away
                # Claiming failed, which usually means the database is
                # unreachable. Sleep and try again rather than crash-looping
                # the process, which on Fly means a restart storm.
                logger.exception("could not claim jobs; retrying shortly")
                ran = 0

            if ran == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=IDLE_SLEEP_SECONDS)


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )

    engine = get_engine()
    runner = Runner(engine)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            # Windows has no SIGTERM handler. Not a problem: the worker runs on
            # Fly, and locally Ctrl-C raises KeyboardInterrupt anyway.
            loop.add_signal_handler(sig, runner.request_stop)

    try:
        await runner.run_forever()
    finally:
        await dispose_engine()


def entrypoint() -> None:
    """`python -m mabel_worker.runner`, the `worker` process in fly.toml."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    entrypoint()
