"""Job `followup`: gestiona cadencias de recontacto.

Logica:
1. Cuando un lead pasa a CONTACTED y no respondio en 5 dias,
   creamos un FollowUpTask programado.
2. Cuando un FollowUpTask vence (scheduled_for <= now), el LLM genera un
   mensaje de seguimiento con angulo distinto al original.
3. Persistimos el Message como follow-up, lo dejamos `sent=False` para que
   el equipo lo envie manualmente desde la UI.

Cadencia por defecto: 5, 14, 30 dias despues del primer contacto.
Limite: maximo 3 follow-ups por lead. Si despues no contesta se descarta.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    FollowUpStatus,
    FollowUpTask,
    JobRun,
    Lead,
    LeadStatus,
    Message,
    MessageChannel,
)
from app.llm.base import CatalogService, GeneratedMessage, LeadContext
from app.llm.factory import get_llm_client
from app.rag.retriever import list_all_catalog


logger = logging.getLogger(__name__)


FOLLOWUP_OFFSETS_DAYS = (5, 14, 30)
MAX_FOLLOWUPS_PER_LEAD = 3


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
        site_analysis=lead.site_analysis,
        pain_points=lead.pain_points,
        recommended_service=lead.recommended_service,
    )


def _pick_channel(lead: Lead) -> str:
    """Prefiere WhatsApp si hay tel, sino email."""
    if lead.phone:
        return "whatsapp"
    if lead.email:
        return "email"
    return "whatsapp"  # fallback


def _msg_to_generated(m: Message) -> GeneratedMessage:
    return GeneratedMessage(
        channel=m.channel.value,
        body=m.body,
        subject=m.subject,
        model=m.model or "",
        prompt_version=m.prompt_version or "",
    )


@dataclass(slots=True)
class FollowUpStats:
    scheduled: int = 0
    executed: int = 0
    skipped: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict:
        return {
            "scheduled": self.scheduled,
            "executed": self.executed,
            "skipped": self.skipped,
            "errors": self.errors or [],
        }


async def schedule_followups_for_lead(
    session: AsyncSession,
    lead: Lead,
    *,
    base_dt: datetime | None = None,
) -> list[FollowUpTask]:
    """Programa los 3 followups (5/14/30 dias) para un lead que acaba de ser
    contacted. Idempotente: si ya hay tasks pendientes para el lead, no duplica.
    """
    base_dt = base_dt or datetime.now(timezone.utc)
    existing = await session.execute(
        select(FollowUpTask).where(
            FollowUpTask.lead_id == lead.id,
            FollowUpTask.status == FollowUpStatus.PENDING,
        )
    )
    if existing.scalars().first():
        return []

    tasks: list[FollowUpTask] = []
    for offset in FOLLOWUP_OFFSETS_DAYS:
        t = FollowUpTask(
            lead_id=lead.id,
            scheduled_for=base_dt + timedelta(days=offset),
            kind="followup",
            status=FollowUpStatus.PENDING,
        )
        session.add(t)
        tasks.append(t)
    # Setear next_followup_at al primero
    lead.next_followup_at = tasks[0].scheduled_for if tasks else None
    return tasks


async def run_pending_followups(
    session: AsyncSession,
    *,
    max_tasks: int | None = None,
    now: datetime | None = None,
) -> FollowUpStats:
    """Ejecuta los FollowUpTask pendientes cuya scheduled_for <= now."""
    now = now or datetime.now(timezone.utc)
    job = JobRun(job_name="followup", started_at=now)
    session.add(job)
    await session.flush()

    stmt = (
        select(FollowUpTask)
        .where(
            FollowUpTask.status == FollowUpStatus.PENDING,
            FollowUpTask.scheduled_for <= now,
        )
        .order_by(FollowUpTask.scheduled_for)
    )
    if max_tasks:
        stmt = stmt.limit(max_tasks)
    result = await session.execute(stmt)
    pending = list(result.scalars().all())

    stats = FollowUpStats(errors=[])
    if not pending:
        job.finished_at = datetime.now(timezone.utc)
        job.success = True
        await session.commit()
        return stats

    catalog_items = await list_all_catalog(session)
    catalog = [
        CatalogService(
            slug=i.slug, name=i.name, short_description=i.short_description,
            long_description=i.long_description, target_audience=i.target_audience,
            price_range=i.price_range,
        ) for i in catalog_items
    ]
    client = get_llm_client()

    for task in pending:
        # Cargar lead con messages (necesarios para angulo y previous_messages)
        result = await session.execute(
            select(Lead).options(selectinload(Lead.messages)).where(Lead.id == task.lead_id)
        )
        lead = result.scalar_one_or_none()
        if lead is None:
            task.status = FollowUpStatus.SKIPPED
            task.note = "lead no existe"
            task.done_at = datetime.now(timezone.utc)
            stats.skipped += 1
            continue

        # No mandar followup si el cliente respondio o se descarto
        if lead.status in (LeadStatus.REPLIED, LeadStatus.DISCARDED):
            task.status = FollowUpStatus.SKIPPED
            task.note = f"lead esta en estado {lead.status.value}"
            task.done_at = datetime.now(timezone.utc)
            stats.skipped += 1
            continue

        # Limite de followups por lead
        prev_followups = sum(
            1 for m in lead.messages
            if (m.prompt_version or "").startswith("followup-")
        )
        if prev_followups >= MAX_FOLLOWUPS_PER_LEAD:
            task.status = FollowUpStatus.SKIPPED
            task.note = "limite de followups alcanzado"
            task.done_at = datetime.now(timezone.utc)
            stats.skipped += 1
            continue

        channel = _pick_channel(lead)
        previous = [
            _msg_to_generated(m) for m in
            sorted(lead.messages, key=lambda x: x.generated_at)
        ]
        days_since = (datetime.now(timezone.utc) - (
            previous[-1].body and lead.last_scraped_at or now
        )).days if previous else 5

        try:
            ctx = _lead_to_context(lead)
            follow = await client.generate_followup(
                ctx, channel, catalog,
                previous_messages=previous,
                days_since_last_contact=max(1, days_since),
            )
        except Exception as exc:
            err = f"task#{task.id} lead#{lead.id}: {exc!r}"
            logger.exception("Fallo generate_followup")
            task.status = FollowUpStatus.FAILED
            task.note = str(exc)[:500]
            task.done_at = datetime.now(timezone.utc)
            stats.errors.append(err)
            continue

        # Persistir como Message
        msg = Message(
            lead_id=lead.id,
            channel=MessageChannel(channel),
            subject=follow.subject,
            body=follow.body,
            model=follow.model,
            prompt_version=follow.prompt_version,
            sent=False,
        )
        session.add(msg)
        await session.flush()

        task.message_id = msg.id
        task.status = FollowUpStatus.DONE
        task.done_at = datetime.now(timezone.utc)
        task.note = f"angle={follow.angle}"
        stats.executed += 1

        # Avanzar next_followup_at al siguiente pendiente
        next_task = await session.execute(
            select(FollowUpTask)
            .where(
                FollowUpTask.lead_id == lead.id,
                FollowUpTask.status == FollowUpStatus.PENDING,
                FollowUpTask.id != task.id,
            )
            .order_by(FollowUpTask.scheduled_for)
            .limit(1)
        )
        nxt = next_task.scalar_one_or_none()
        lead.next_followup_at = nxt.scheduled_for if nxt else None

    job.finished_at = datetime.now(timezone.utc)
    job.success = not stats.errors
    job.items_processed = stats.executed + stats.skipped
    if stats.errors:
        job.error = "\n".join(stats.errors)

    await session.commit()
    return stats
