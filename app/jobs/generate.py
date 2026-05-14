"""Job `generate`: para cada lead sin mensaje generado, llama al LLM y persiste.

Usa el LLMClient + el catalogo cargado en DB. Se puede invocar:
- por lead individual: `generate_for_lead(...)`
- en bulk para todos los leads pendientes: `run_generate_all(...)`
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import JobRun, Lead, LeadStatus, Message, MessageChannel
from app.llm.base import CatalogService, Channel, LeadContext
from app.llm.factory import get_llm_client
from app.rag.retriever import list_all_catalog, search_catalog


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


async def _select_catalog_for_lead(
    session: AsyncSession, lead: Lead, *, top_k: int = 4
) -> list[CatalogService]:
    q_parts = [
        lead.name or "",
        lead.category or "",
        lead.city or "",
        lead.qualification_reason or "",
        "sin web" if not lead.website else "",
    ]
    hits = await search_catalog(session, " ".join(q_parts), limit=top_k)
    all_items = await list_all_catalog(session)
    by_id = {i.id: i for i in all_items}
    selected = [by_id[h.id] for h in hits if h.id in by_id]
    if not selected:
        selected = all_items[:3]
    return [
        CatalogService(
            slug=i.slug,
            name=i.name,
            short_description=i.short_description,
            long_description=i.long_description,
            target_audience=i.target_audience,
            price_range=i.price_range,
        )
        for i in selected
    ]


async def generate_for_lead(
    session: AsyncSession,
    lead: Lead,
    *,
    channels: tuple[Channel, ...] = ("whatsapp", "email"),
) -> list[Message]:
    """Genera y persiste mensajes para un lead. Reemplaza los existentes del mismo canal."""
    catalog = await _select_catalog_for_lead(session, lead)
    ctx = _lead_to_context(lead)
    client = get_llm_client()

    new_messages: list[Message] = []
    for channel in channels:
        gen = await client.generate_message(ctx, channel, catalog)
        msg = Message(
            lead_id=lead.id,
            channel=MessageChannel(channel),
            subject=gen.subject,
            body=gen.body,
            model=gen.model,
            prompt_version=gen.prompt_version,
            sent=False,
        )
        session.add(msg)
        new_messages.append(msg)

    # Marcar como qualified al menos si pasamos por aqui, salvo que ya este contacted/replied
    if lead.status == LeadStatus.NEW:
        lead.status = LeadStatus.QUALIFIED
        lead.qualified = True
        if not lead.qualification_reason:
            lead.qualification_reason = "sin sitio web" if not lead.website else "lead manual"

    await session.flush()
    return new_messages


async def run_generate_all(
    session: AsyncSession,
    *,
    max_leads: int | None = None,
) -> dict:
    """Genera mensajes para todos los leads activos sin mensaje aun.

    Considera "sin mensaje" si no tienen ningun Message asociado.
    """
    job = JobRun(job_name="generate", started_at=datetime.now(timezone.utc))
    session.add(job)
    await session.flush()

    # Leads calificados sin mensajes. El bulk no contacta leads "new" sin enrich:
    # primero tienen que pasar por website_check para confirmar oportunidad real.
    stmt = (
        select(Lead)
        .options(selectinload(Lead.messages))
        .where(Lead.status == LeadStatus.QUALIFIED)
        .where(Lead.qualified.is_(True))
        .order_by(Lead.created_at)
    )
    if max_leads:
        stmt = stmt.limit(max_leads)

    result = await session.execute(stmt)
    candidates = [l for l in result.scalars().all() if not l.messages]

    generated = 0
    errors: list[str] = []
    for lead in candidates:
        try:
            await generate_for_lead(session, lead)
            generated += 1
        except Exception as exc:
            err = f"lead#{lead.id} ({lead.name}): {exc!r}"
            logger.exception("Falla en generate para %s", lead.name)
            errors.append(err)

    job.finished_at = datetime.now(timezone.utc)
    job.success = not errors
    job.items_processed = generated
    if errors:
        job.error = "\n".join(errors)

    await session.commit()
    return {
        "candidates": len(candidates),
        "generated": generated,
        "errors": errors,
    }
