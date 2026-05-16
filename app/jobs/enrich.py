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
from app.llm.base import CatalogService, LeadContext
from app.llm.factory import get_llm_client
from app.rag.retriever import list_all_catalog


logger = logging.getLogger(__name__)


def _lead_to_context(lead: Lead) -> LeadContext:
    return LeadContext(
        name=lead.name,
        category=lead.category,
        city=lead.city,
        province=lead.province,
        country=lead.country or "AR",
        has_website=bool(lead.website),
        website=lead.website,
        website_status=lead.website_status,
        rating=lead.rating,
        qualification_reason=lead.qualification_reason,
    )


async def _enrich_lead(
    session: AsyncSession,
    lead: Lead,
    catalog: list[CatalogService],
    *,
    run_llm: bool = True,
) -> WebsiteAssessment:
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

    # Intel del LLM: analisis profundo del sitio + scoring + extraccion ofuscada
    # de contactos. Solo si el lead califica (no quemamos tokens en sitios "ok").
    if run_llm and lead.qualified:
        try:
            client = get_llm_client()
            ctx = _lead_to_context(lead)
            intel = await client.analyze_lead(
                ctx,
                catalog,
                site_html_excerpt=result.body_excerpt or "",
                website_status=result.status_tag or "",
            )
            lead.priority_score = intel.priority_score
            lead.priority_reason = intel.priority_reason
            lead.site_analysis = intel.site_analysis
            lead.pain_points = " | ".join(intel.pain_points) if intel.pain_points else None
            lead.recommended_service = intel.recommended_service
            # Solo guardamos contactos extra si encontro algo nuevo
            if intel.extracted_emails:
                lead.extracted_emails = " | ".join(intel.extracted_emails)
                # Si no teniamos email y el LLM saco uno valido, lo promovemos
                if not lead.email:
                    valid = [e for e in intel.extracted_emails if "@" in e and "." in e.split("@")[-1]]
                    if valid:
                        lead.email = valid[0]
            if intel.extracted_phones:
                lead.extracted_phones = " | ".join(intel.extracted_phones)
        except Exception:
            logger.exception("analyze_lead fallo para %s (sigue con heuristica simple)", lead.name)

    lead.last_enriched_at = datetime.now(timezone.utc)
    return result


async def run_enrich(
    session: AsyncSession,
    *,
    only_pending: bool = True,
    max_leads: int | None = None,
    concurrency: int = 3,
    run_llm: bool = True,
) -> dict:
    """Enriquece leads en bulk. `only_pending=True` salta los ya enriched.

    Si run_llm=True (default), ademas del website check llama al LLM para
    obtener: priority_score, site_analysis, pain_points, recommended_service,
    extracted_emails/phones. Solo para leads que califican (no quemar tokens
    en sitios sanos).
    """
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
        return {"checked": 0, "qualified": 0, "scored": 0, "errors": []}

    # Cargar catalogo una sola vez, lo comparten todas las llamadas analyze_lead
    catalog_items = await list_all_catalog(session)
    catalog = [
        CatalogService(
            slug=i.slug,
            name=i.name,
            short_description=i.short_description,
            long_description=i.long_description,
            target_audience=i.target_audience,
            price_range=i.price_range,
        )
        for i in catalog_items
    ]

    semaphore = asyncio.Semaphore(concurrency)
    counters: dict = {"qualified": 0, "scored": 0, "errors": []}

    async def _one(lead: Lead) -> None:
        async with semaphore:
            try:
                assess = await _enrich_lead(session, lead, catalog, run_llm=run_llm)
                if assess.qualifies:
                    counters["qualified"] += 1
                if lead.priority_score is not None:
                    counters["scored"] += 1
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
        "scored": counters["scored"],
        "errors": counters["errors"],
    }
