"""APScheduler in-process que corre los jobs automaticamente.

Frecuencias (en runtime real, AR):
- discover: 1 vez por dia + 1 ejecucion al arranque si paso >24h desde la ultima
- enrich:   cada 2h
- generate: cada 1h

Todos respetan idempotencia: si no hay leads nuevos / pendientes, no hacen nada.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import desc, select

from app.db.models import JobRun
from app.db.session import session_scope
from app.jobs.discover import run_discover
from app.jobs.enrich import run_enrich
from app.jobs.generate import run_generate_all
from app.scrapers.factory import get_lead_provider


logger = logging.getLogger(__name__)


async def _job_discover() -> None:
    async with session_scope() as session:
        provider = get_lead_provider()
        stats = await run_discover(session, provider)
        logger.info("[scheduler] discover -> %s", stats.to_dict())


async def _job_enrich() -> None:
    async with session_scope() as session:
        stats = await run_enrich(session, only_pending=True)
        logger.info("[scheduler] enrich -> %s", stats)


async def _job_generate() -> None:
    async with session_scope() as session:
        stats = await run_generate_all(session)
        logger.info("[scheduler] generate -> %s", stats)


async def _last_run(job_name: str) -> datetime | None:
    async with session_scope() as session:
        result = await session.execute(
            select(JobRun)
            .where(JobRun.job_name == job_name, JobRun.success.is_(True))
            .order_by(desc(JobRun.finished_at))
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row.finished_at if row else None


async def maybe_run_discover_at_startup() -> None:
    """Si pasaron >24h desde la ultima ejecucion exitosa, arranca un discover ya."""
    last = await _last_run("discover")
    if last is None or (datetime.now(timezone.utc) - last) > timedelta(hours=24):
        logger.info("[scheduler] arranque: corriendo discover inmediato (last=%s)", last)
        try:
            await _job_discover()
            await _job_enrich()
            await _job_generate()
        except Exception:
            logger.exception("[scheduler] fallo en cadena de arranque")


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="America/Argentina/Buenos_Aires")
    # Discover: una vez por dia 09:00 AR. Ajustable.
    scheduler.add_job(
        _job_discover,
        trigger=CronTrigger(hour=9, minute=0),
        id="discover_daily",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # Enrich: cada 2h
    scheduler.add_job(
        _job_enrich,
        trigger=IntervalTrigger(hours=2),
        id="enrich_periodic",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # Generate: cada 1h
    scheduler.add_job(
        _job_generate,
        trigger=IntervalTrigger(hours=1),
        id="generate_periodic",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler
