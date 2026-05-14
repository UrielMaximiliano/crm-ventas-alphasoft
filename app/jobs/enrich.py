"""Job `enrich`: revisa el sitio web de cada lead pendiente y marca calificacion.

Para cada lead con status=NEW (o cualquier lead sin enrich previo):
- Si no tiene web -> qualified=True, reason="sin sitio web"
- Si tiene web, chequea heuristicas via `assess_website`.
- Setea lead.website_status, lead.qualification_reason, lead.qualified,
  y avanza el status NEW -> QUALIFIED si pasa la heuristica.
- Actualiza lead.last_enriched_at.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JobRun, Lead, LeadStatus
from app.enrich.website_check import WebsiteAssessment, assess_no_website, assess_website


logger = logging.getLogger(__name__)


async def _enrich_lead(session: AsyncSession, lead: Lead) -> WebsiteAssessment:
    if not lead.website:
        result = assess_no_website()
    else:
        result = await assess_website(lead.website)

    lead.website_status = result.status_tag
    if result.qualifies:
        lead.qualified = True
        lead.qualification_reason = result.reason
        if lead.status == LeadStatus.NEW:
            lead.status = LeadStatus.QUALIFIED
    else:
        # No descalifica leads ya marcados qualified por otra razon,
        # solo deja de proponerlos para mensajes nuevos
        if lead.status == LeadStatus.NEW and not lead.qualified:
            lead.qualified = False
            lead.qualification_reason = None

    # Si scrapeamos un email del HTML del sitio y el lead no lo tenia, lo guardamos.
    # Maps casi nunca expone email, asi que esta es la fuente principal.
    if not lead.email and result.emails:
        lead.email = result.emails[0]

    lead.last_enriched_at = datetime.now(timezone.utc)
    return result


async def run_enrich(
    session: AsyncSession,
    *,
    only_pending: bool = True,
    max_leads: int | None = None,
    concurrency: int = 5,
) -> dict:
    """Enriquece leads en bulk. `only_pending=True` salta los ya enriched."""
    job = JobRun(job_name="enrich", started_at=datetime.now(timezone.utc))
    session.add(job)
    await session.flush()

    stmt = select(Lead).order_by(Lead.created_at)
    if only_pending:
        stmt = stmt.where(Lead.last_enriched_at.is_(None))
    if max_leads:
        stmt = stmt.limit(max_leads)

    result = await session.execute(stmt)
    leads = list(result.scalars().all())

    if not leads:
        job.finished_at = datetime.now(timezone.utc)
        job.success = True
        await session.commit()
        return {"checked": 0, "qualified": 0, "errors": []}

    semaphore = asyncio.Semaphore(concurrency)
    counters = {"qualified": 0, "errors": []}

    async def _one(lead: Lead) -> None:
        async with semaphore:
            try:
                assess = await _enrich_lead(session, lead)
                if assess.qualifies:
                    counters["qualified"] += 1
            except Exception as exc:
                err = f"lead#{lead.id} ({lead.name}): {exc!r}"
                logger.exception("Falla enrich de %s", lead.name)
                counters["errors"].append(err)

    await asyncio.gather(*(_one(l) for l in leads))

    job.finished_at = datetime.now(timezone.utc)
    job.success = not counters["errors"]
    job.items_processed = len(leads)
    if counters["errors"]:
        job.error = "\n".join(counters["errors"])

    await session.commit()
    return {
        "checked": len(leads),
        "qualified": counters["qualified"],
        "errors": counters["errors"],
    }
