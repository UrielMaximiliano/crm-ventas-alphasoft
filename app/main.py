from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from datetime import datetime, timezone

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import (
    FollowUpStatus,
    FollowUpTask,
    Lead,
    LeadNote,
    LeadStatus,
    LeadTag,
    Message,
    MessageChannel,
    ReplyClassification,
    ReplyIntent,
)
from app.db.session import get_session, session_scope
from app.exports import build_leads_filename, build_leads_xlsx
from app.jobs.discover import run_discover
from app.jobs.enrich import run_enrich
from app.jobs.followup import (
    run_pending_followups,
    schedule_followups_for_lead,
)
from app.jobs.generate import generate_for_lead, run_generate_all
from app.scheduler import build_scheduler, maybe_run_discover_at_startup
from app.llm.base import CatalogService, Channel, GeneratedMessage, LeadContext
from app.llm.factory import get_llm_client
from app.rag.catalog import seed_catalog
from app.rag.retriever import list_all_catalog, search_catalog
from app.scrapers.factory import get_lead_provider


logger = logging.getLogger("crm")


# ----- Plantillas -----
TEMPLATES_DIR = Path(__file__).resolve().parent / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _run_migrations() -> None:
    settings = get_settings()
    cfg = AlembicConfig(str(settings.repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(settings.repo_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.sync_database_url)
    logger.info("Aplicando migraciones Alembic...")
    command.upgrade(cfg, "head")
    logger.info("Migraciones aplicadas.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    _run_migrations()

    # Seed del catalogo (idempotente por hash del YAML)
    async with session_scope() as session:
        result = await seed_catalog(session)
    logger.info("Seed catalogo: %s", result)

    # Scheduler: solo se enciende si AUTOSTART_JOBS=true. Por default queda OFF
    # asi el equipo dispara discover / enrich / generate manualmente desde la UI.
    if settings.autostart_jobs:
        scheduler = build_scheduler()
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info(
            "Scheduler activo: discover diario 09:00 AR / enrich 2h / generate 1h"
        )
        try:
            await maybe_run_discover_at_startup()
        except Exception:
            logger.exception("Fallo en cadena de arranque del scheduler")
    else:
        app.state.scheduler = None
        logger.info(
            "Scheduler DESACTIVADO (AUTOSTART_JOBS=0). "
            "Disparar jobs manualmente desde la UI o /api/jobs/*."
        )

    logger.info(
        "App iniciada (mock_llm=%s mock_scraper=%s autostart=%s)",
        settings.mock_llm,
        settings.mock_scraper,
        settings.autostart_jobs,
    )
    yield
    try:
        sched = getattr(app.state, "scheduler", None)
        if sched is not None:
            sched.shutdown(wait=False)
    except Exception:
        pass
    logger.info("App apagandose.")


app = FastAPI(
    title="CRM Ventas Alphasoft",
    description="Agente de prospeccion comercial autohosted (Argentina).",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/api/info")
async def info() -> JSONResponse:
    s = get_settings()
    return JSONResponse(
        {
            "app": "crm-ventas-alphasoft",
            "version": "0.1.0",
            "mock_llm": s.mock_llm,
            "mock_scraper": s.mock_scraper,
            "groq_model": s.groq_model,
        }
    )


async def _fetch_leads(
    session: AsyncSession,
    *,
    city: str | None = None,
    category: str | None = None,
    status: str | None = None,
    only_no_web: bool = False,
    limit: int = 100,
) -> list[Lead]:
    # Orden: primero por priority_score DESC (mas prometedores arriba), despues
    # por created_at DESC. NULLS LAST en score para que los todavia-no-analizados
    # caigan abajo.
    stmt = (
        select(Lead)
        .options(
            selectinload(Lead.messages),
            selectinload(Lead.notes),
            selectinload(Lead.tags),
            selectinload(Lead.follow_ups),
            selectinload(Lead.reply_classifications),
        )
        .order_by(Lead.priority_score.desc().nulls_last(), desc(Lead.created_at))
    )
    if city:
        stmt = stmt.where(Lead.city == city)
    if category:
        stmt = stmt.where(Lead.category == category)
    if status:
        try:
            stmt = stmt.where(Lead.status == LeadStatus(status))
        except ValueError:
            pass
    if only_no_web:
        stmt = stmt.where(Lead.website.is_(None))
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _facets(session: AsyncSession) -> dict[str, list[str]]:
    """Devuelve listas de ciudades / categorias para los filtros de la UI."""
    cities = await session.execute(
        select(Lead.city).where(Lead.city.is_not(None)).distinct().order_by(Lead.city)
    )
    cats = await session.execute(
        select(Lead.category).where(Lead.category.is_not(None)).distinct().order_by(Lead.category)
    )
    return {
        "cities": [c for (c,) in cities.all() if c],
        "categories": [c for (c,) in cats.all() if c],
    }


async def _get_lead_or_404(session: AsyncSession, lead_id: int) -> Lead:
    result = await session.execute(
        select(Lead)
        .options(
            selectinload(Lead.messages),
            selectinload(Lead.notes),
            selectinload(Lead.tags),
            selectinload(Lead.follow_ups),
            selectinload(Lead.reply_classifications),
        )
        .where(Lead.id == lead_id)
    )
    lead = result.scalar_one_or_none()
    if lead is None:
        raise HTTPException(404, f"lead {lead_id} no encontrado")
    return lead


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    city: str | None = None,
    category: str | None = None,
    status: str | None = None,
    only_no_web: bool = False,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    settings = get_settings()
    leads = await _fetch_leads(
        session,
        city=city,
        category=category,
        status=status,
        only_no_web=only_no_web,
    )
    lead_views = [_lead_to_view(l) for l in leads]
    facets = await _facets(session)
    return templates.TemplateResponse(
        request=request,
        name="leads.html",
        context={
            "leads": lead_views,
            "stats": _lead_stats(lead_views),
            "mock": settings.mock_scraper,
            "mock_llm": settings.mock_llm,
            "facets": facets,
            "filters": {
                "city": city,
                "category": category,
                "status": status,
                "only_no_web": only_no_web,
            },
        },
    )


def _lead_priority(lead: Lead) -> tuple[str, str]:
    reason = " ".join(
        part for part in [lead.qualification_reason, lead.website_status] if part
    ).lower()
    if lead.status == LeadStatus.DISCARDED:
        return "Descartado", "slate"
    if lead.status == LeadStatus.NEW and lead.last_enriched_at is None:
        return "Pendiente", "slate"
    if not lead.phone and not lead.email:
        return "Sin contacto", "amber"
    if (
        not lead.website
        or "sin sitio" in reason
        or "sitio propio" in reason
        or "link-social" in reason
        or "whatsapp" in reason
        or "caido" in reason
        or "sin dns" in reason
        or "error" in reason
    ):
        return "Alta", "rose"
    if any(
        token in reason
        for token in ["desactualizada", "viejo", "wix", "wordpress", "no-mobile", "lenta"]
    ):
        return "Media", "amber"
    if lead.qualified:
        return "Media", "amber"
    return "Baja", "slate"


def _next_action(lead: Lead, messages: list[dict]) -> str:
    if lead.status == LeadStatus.DISCARDED:
        return "Descartado"
    if not lead.phone and not lead.email:
        return "Buscar contacto"
    if lead.status == LeadStatus.NEW and lead.last_enriched_at is None:
        return "Verificar web"
    if not messages:
        return "Generar mensaje" if lead.qualified else "Revisar lead"
    if lead.status == LeadStatus.CONTACTED or all(m["sent"] for m in messages):
        return "Esperar respuesta"
    return "Copiar y contactar"


def _lead_to_view(lead: Lead) -> dict:
    # Mostrar solo el ultimo mensaje por canal (el LLM se puede regenerar)
    latest_by_channel: dict[str, Message] = {}
    for m in sorted(lead.messages or [], key=lambda x: x.generated_at):
        latest_by_channel[m.channel.value] = m
    messages = [
        {
            "id": m.id,
            "channel": m.channel.value,
            "subject": m.subject,
            "body": m.body,
            "sent": m.sent,
        }
        for m in latest_by_channel.values()
    ]
    priority_label, priority_tone = _lead_priority(lead)
    source_label = "Google Maps" if lead.source == "google_maps" else "Demo"
    return {
        "id": lead.id,
        "name": lead.name,
        "category": lead.category,
        "city": lead.city,
        "province": lead.province,
        "country": lead.country,
        "phone": lead.phone,
        "email": lead.email,
        "website": lead.website,
        "website_status": lead.website_status,
        "address": lead.address,
        "rating": lead.rating,
        "reviews_count": lead.reviews_count,
        "source": lead.source,
        "source_label": source_label,
        "source_id": lead.source_id,
        "search_query": lead.search_query,
        "status": lead.status.value if lead.status else "new",
        "qualified": lead.qualified,
        "qualification_reason": lead.qualification_reason,
        "messages": messages,
        "priority_label": priority_label,
        "priority_tone": priority_tone,
        "next_action": _next_action(lead, messages),
        "has_contact": bool(lead.phone or lead.email),
        "has_website": bool(lead.website),
        # Intel del LLM (puede ser None si todavia no se enriched con analyze_lead)
        "priority_score": lead.priority_score,
        "priority_score_reason": lead.priority_reason,
        "site_analysis": lead.site_analysis,
        "pain_points": [
            p.strip() for p in (lead.pain_points or "").split("|") if p.strip()
        ],
        "recommended_service": lead.recommended_service,
        "extracted_emails": [
            e.strip() for e in (lead.extracted_emails or "").split("|") if e.strip()
        ],
        "extracted_phones": [
            p.strip() for p in (lead.extracted_phones or "").split("|") if p.strip()
        ],
        # Workflow comercial
        "next_followup_at": (
            lead.next_followup_at.isoformat() if lead.next_followup_at else None
        ),
        "last_reply_at": (
            lead.last_reply_at.isoformat() if lead.last_reply_at else None
        ),
        "conversion_value_estimate": lead.conversion_value_estimate,
        "tags": [t.tag for t in (lead.tags or [])],
        "notes": [
            {
                "id": n.id,
                "body": n.body,
                "author": n.author,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in (lead.notes or [])
        ],
        "follow_ups": [
            {
                "id": f.id,
                "scheduled_for": f.scheduled_for.isoformat() if f.scheduled_for else None,
                "status": f.status.value if f.status else None,
                "kind": f.kind,
                "message_id": f.message_id,
                "note": f.note,
            }
            for f in (lead.follow_ups or [])
        ],
        "last_reply_classification": (
            {
                "intent": lead.reply_classifications[0].intent.value,
                "sentiment": lead.reply_classifications[0].sentiment,
                "summary": lead.reply_classifications[0].summary,
                "suggested_action": lead.reply_classifications[0].suggested_action,
                "suggested_reply": lead.reply_classifications[0].suggested_reply,
                "created_at": lead.reply_classifications[0].created_at.isoformat()
                              if lead.reply_classifications[0].created_at else None,
            }
            if lead.reply_classifications else None
        ),
    }


def _lead_stats(leads: list[dict]) -> dict[str, int]:
    return {
        "total": len(leads),
        "ready": sum(1 for l in leads if l["next_action"] == "Copiar y contactar"),
        "needs_message": sum(1 for l in leads if l["next_action"] == "Generar mensaje"),
        "no_web": sum(1 for l in leads if not l["has_website"]),
        "contacted": sum(1 for l in leads if l["status"] == "contacted"),
    }


# ----- API: leads -----

@app.get("/api/leads")
async def api_leads(
    city: str | None = None,
    category: str | None = None,
    status: str | None = None,
    only_no_web: bool = False,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    leads = await _fetch_leads(
        session,
        city=city,
        category=category,
        status=status,
        only_no_web=only_no_web,
        limit=limit,
    )
    return JSONResponse([_lead_to_view(l) for l in leads])


@app.get("/api/leads/export.xlsx")
async def api_leads_export_xlsx(
    city: str | None = None,
    category: str | None = None,
    status: str | None = None,
    only_no_web: bool = False,
    limit: int = Query(1000, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Devuelve un xlsx con todos los leads filtrados.

    Respeta los mismos filtros que `/api/leads` y `/`. El equipo lo descarga,
    lo abre en Excel/LibreOffice/Google Sheets y trabaja desde ahi.
    """
    leads = await _fetch_leads(
        session,
        city=city,
        category=category,
        status=status,
        only_no_web=only_no_web,
        limit=limit,
    )
    xlsx_bytes = build_leads_xlsx(leads)
    filename = build_leads_filename()
    return Response(
        content=xlsx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/leads/{lead_id}")
async def api_lead_detail(
    lead_id: int,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    lead = await _get_lead_or_404(session, lead_id)
    return JSONResponse(_lead_to_view(lead))


# ----- API: jobs -----

@app.post("/api/jobs/discover")
async def api_jobs_discover(
    max_queries: int | None = Query(None, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    provider = get_lead_provider()
    stats = await run_discover(session, provider, max_queries=max_queries)
    return JSONResponse(
        {
            "provider": provider.name,
            **stats.to_dict(),
        }
    )


@app.post("/api/jobs/generate")
async def api_jobs_generate(
    max_leads: int | None = Query(None, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    stats = await run_generate_all(session, max_leads=max_leads)
    return JSONResponse(stats)


@app.post("/api/jobs/enrich")
async def api_jobs_enrich(
    only_pending: bool = True,
    max_leads: int | None = Query(None, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    stats = await run_enrich(session, only_pending=only_pending, max_leads=max_leads)
    return JSONResponse(stats)


@app.post("/api/jobs/followups")
async def api_jobs_followups(
    max_tasks: int | None = Query(None, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    stats = await run_pending_followups(session, max_tasks=max_tasks)
    return JSONResponse(stats.to_dict())


# ===== Sugerencia de queries con LLM =====

class SuggestQueriesIn(BaseModel):
    country: str = "AR"
    focus: str = "PyMEs con baja madurez digital"
    count: int = Field(default=10, ge=1, le=30)


def _append_query_to_yaml(rubro: str, city: str, province: str | None) -> bool:
    """Agrega una query al archivo data/search_queries.yml. Devuelve True si la
    agrego, False si ya existia o el archivo no se pudo escribir."""
    settings = get_settings()
    path = settings.data_dir / "search_queries.yml"
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    needle = f"rubro: {rubro}"
    # Si ya existe la combinacion rubro+ciudad, no duplicar
    if needle in text and city.lower() in text.lower():
        return False
    rubro_clean = rubro.strip().lower()
    city_clean = city.strip()
    prov_clean = (province or "").strip()
    line = (
        f"  - {{ rubro: {rubro_clean}, city: {city_clean}, "
        f"province: {prov_clean} }}\n"
    )
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
        return True
    except OSError:
        return False


@app.post("/api/llm/suggest-queries")
async def api_llm_suggest_queries(
    payload: SuggestQueriesIn,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Devuelve N combinaciones rubro+ciudad para prospectar.

    Excluye las queries que ya estan en el yml + las que ya estan en DB.
    """
    # Cargar queries existentes para evitar duplicados
    from app.jobs.discover import _load_queries  # import local para evitar ciclo
    _, existing = _load_queries()
    existing_texts = [q.text for q in existing]
    # Tambien los search_query ya en DB
    db_rows = await session.execute(
        select(Lead.search_query).where(Lead.search_query.is_not(None)).distinct()
    )
    existing_texts.extend([r[0] for r in db_rows.all() if r[0]])

    client = get_llm_client()
    suggested = await client.suggest_queries(
        country=payload.country,
        focus=payload.focus,
        existing_queries=existing_texts,
        count=payload.count,
    )
    return JSONResponse(
        [
            {
                "rubro": s.rubro,
                "city": s.city,
                "province": s.province,
                "reason": s.reason,
            }
            for s in suggested
        ]
    )


# ===== Notas por lead =====

class NoteIn(BaseModel):
    body: str
    author: str | None = None


@app.post("/api/leads/{lead_id}/notes")
async def api_lead_add_note(
    lead_id: int,
    payload: NoteIn,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    lead = await _get_lead_or_404(session, lead_id)
    note = LeadNote(lead_id=lead.id, body=payload.body, author=payload.author)
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return JSONResponse(
        {
            "id": note.id,
            "lead_id": note.lead_id,
            "body": note.body,
            "author": note.author,
            "created_at": note.created_at.isoformat(),
        }
    )


@app.delete("/api/leads/{lead_id}/notes/{note_id}")
async def api_lead_delete_note(
    lead_id: int,
    note_id: int,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    result = await session.execute(
        select(LeadNote).where(LeadNote.id == note_id, LeadNote.lead_id == lead_id)
    )
    note = result.scalar_one_or_none()
    if note is None:
        raise HTTPException(404, "nota no encontrada")
    await session.delete(note)
    await session.commit()
    return JSONResponse({"deleted": True})


# ===== Tags =====

class TagIn(BaseModel):
    tag: str


def _normalize_tag(tag: str) -> str:
    return tag.strip().lower().replace(" ", "-")[:64]


@app.post("/api/leads/{lead_id}/tags")
async def api_lead_add_tag(
    lead_id: int,
    payload: TagIn,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    lead = await _get_lead_or_404(session, lead_id)
    norm = _normalize_tag(payload.tag)
    if not norm:
        raise HTTPException(400, "tag invalido")
    # Verificar duplicado
    existing = await session.execute(
        select(LeadTag).where(LeadTag.lead_id == lead.id, LeadTag.tag == norm)
    )
    if existing.scalar_one_or_none():
        return JSONResponse({"tag": norm, "already_exists": True})
    tag = LeadTag(lead_id=lead.id, tag=norm)
    session.add(tag)
    await session.commit()
    return JSONResponse({"tag": norm, "already_exists": False})


@app.delete("/api/leads/{lead_id}/tags/{tag}")
async def api_lead_remove_tag(
    lead_id: int,
    tag: str,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    norm = _normalize_tag(tag)
    result = await session.execute(
        select(LeadTag).where(LeadTag.lead_id == lead_id, LeadTag.tag == norm)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "tag no encontrado")
    await session.delete(row)
    await session.commit()
    return JSONResponse({"deleted": True, "tag": norm})


# ===== Clasificacion de respuestas =====

class ClassifyReplyIn(BaseModel):
    raw_reply: str


@app.post("/api/leads/{lead_id}/classify-reply")
async def api_lead_classify_reply(
    lead_id: int,
    payload: ClassifyReplyIn,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    lead = await _get_lead_or_404(session, lead_id)
    # Construir contexto
    ctx = LeadContext(
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
    catalog_items = await list_all_catalog(session)
    catalog = [
        CatalogService(
            slug=i.slug, name=i.name, short_description=i.short_description,
            long_description=i.long_description, target_audience=i.target_audience,
            price_range=i.price_range,
        ) for i in catalog_items
    ]
    previous = [
        GeneratedMessage(
            channel=m.channel.value, body=m.body, subject=m.subject,
            model=m.model or "", prompt_version=m.prompt_version or "",
        ) for m in sorted(lead.messages, key=lambda x: x.generated_at)
    ]

    client = get_llm_client()
    analysis = await client.classify_reply(
        ctx, payload.raw_reply, catalog, previous_messages=previous,
    )

    # Persistir
    rc = ReplyClassification(
        lead_id=lead.id,
        raw_reply=payload.raw_reply,
        intent=ReplyIntent(analysis.intent),
        sentiment=analysis.sentiment,
        summary=analysis.summary,
        suggested_action=analysis.suggested_action,
        suggested_reply=analysis.suggested_reply,
        model=analysis.model,
    )
    session.add(rc)

    # Actualizar status + last_reply_at del lead
    lead.last_reply_at = datetime.now(timezone.utc)
    if analysis.intent in ("not_interested", "spam", "wrong_contact"):
        lead.status = LeadStatus.DISCARDED
    elif lead.status in (LeadStatus.CONTACTED, LeadStatus.QUALIFIED, LeadStatus.NEW):
        lead.status = LeadStatus.REPLIED
    # Cancelar followups pendientes
    pending = await session.execute(
        select(FollowUpTask).where(
            FollowUpTask.lead_id == lead.id,
            FollowUpTask.status == FollowUpStatus.PENDING,
        )
    )
    for t in pending.scalars().all():
        t.status = FollowUpStatus.SKIPPED
        t.note = "lead respondio: " + analysis.intent
        t.done_at = datetime.now(timezone.utc)
    lead.next_followup_at = None

    await session.commit()
    await session.refresh(rc)
    return JSONResponse(
        {
            "id": rc.id,
            "intent": rc.intent.value,
            "sentiment": rc.sentiment,
            "summary": rc.summary,
            "suggested_action": rc.suggested_action,
            "suggested_reply": rc.suggested_reply,
            "lead_status": lead.status.value,
        }
    )


# ===== Follow-ups: schedule manual + generate one-shot =====

class ScheduleFollowupsIn(BaseModel):
    base_dt: datetime | None = None


@app.post("/api/leads/{lead_id}/schedule-followups")
async def api_lead_schedule_followups(
    lead_id: int,
    payload: ScheduleFollowupsIn,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    lead = await _get_lead_or_404(session, lead_id)
    tasks = await schedule_followups_for_lead(session, lead, base_dt=payload.base_dt)
    await session.commit()
    return JSONResponse(
        {
            "lead_id": lead.id,
            "scheduled": len(tasks),
            "tasks": [
                {"id": t.id, "scheduled_for": t.scheduled_for.isoformat()}
                for t in tasks
            ],
            "next_followup_at": (
                lead.next_followup_at.isoformat() if lead.next_followup_at else None
            ),
        }
    )


@app.post("/api/leads/{lead_id}/generate-message")
async def api_lead_generate_message(
    lead_id: int,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    lead = await _get_lead_or_404(session, lead_id)
    messages = await generate_for_lead(session, lead)
    await session.commit()
    return JSONResponse(
        {
            "lead_id": lead.id,
            "generated": [
                {"channel": m.channel.value, "subject": m.subject, "body": m.body}
                for m in messages
            ],
        }
    )


@app.post("/api/leads/{lead_id}/discard")
async def api_lead_discard(
    lead_id: int,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    lead = await _get_lead_or_404(session, lead_id)
    lead.status = LeadStatus.DISCARDED
    await session.commit()
    return JSONResponse({"lead_id": lead.id, "status": lead.status.value})


@app.post("/api/leads/{lead_id}/mark-sent")
async def api_lead_mark_sent(
    lead_id: int,
    channel: Channel | None = None,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    lead = await _get_lead_or_404(session, lead_id)
    now = datetime.now(timezone.utc)
    updated = 0
    for m in lead.messages or []:
        if m.sent:
            continue
        if channel and m.channel.value != channel:
            continue
        m.sent = True
        m.sent_at = now
        updated += 1
    if updated and lead.status in (LeadStatus.NEW, LeadStatus.QUALIFIED):
        lead.status = LeadStatus.CONTACTED
        # Programar cadencia de follow-ups automatica (5/14/30 dias)
        try:
            await schedule_followups_for_lead(session, lead)
        except Exception:
            logger.exception("No se pudieron programar followups para %s", lead.name)
    await session.commit()
    return JSONResponse(
        {"lead_id": lead.id, "status": lead.status.value, "messages_marked": updated}
    )


# ----- UI: rutas con redirect 303 (post/redirect/get) -----

def _redirect_home(request: Request) -> RedirectResponse:
    referer = request.headers.get("referer") or "/"
    return RedirectResponse(url=referer, status_code=303)


@app.post("/ui/jobs/discover")
async def ui_jobs_discover(
    request: Request,
    max_queries: int | None = Query(3, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    provider = get_lead_provider()
    await run_discover(session, provider, max_queries=max_queries)
    return _redirect_home(request)


@app.post("/ui/jobs/generate")
async def ui_jobs_generate(
    request: Request, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    await run_generate_all(session)
    return _redirect_home(request)


@app.post("/ui/jobs/enrich")
async def ui_jobs_enrich(
    request: Request, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    await run_enrich(session, only_pending=True)
    return _redirect_home(request)


@app.post("/ui/jobs/pipeline")
async def ui_jobs_pipeline(
    request: Request,
    max_queries: int | None = Query(3, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Encadena discover -> enrich -> generate en una sola operacion.

    Resuelve la race condition de antes: si el usuario apretaba los 3 botones
    en orden rapido, enrich/generate corrian contra una DB vacia porque
    discover commitea recien al final.
    """
    provider = get_lead_provider()
    await run_discover(session, provider, max_queries=max_queries)
    await run_enrich(session, only_pending=True)
    await run_generate_all(session)
    return _redirect_home(request)


@app.post("/api/jobs/pipeline")
async def api_jobs_pipeline(
    max_queries: int | None = Query(3, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Version JSON de /ui/jobs/pipeline. Devuelve stats de cada etapa."""
    provider = get_lead_provider()
    discover_stats = await run_discover(session, provider, max_queries=max_queries)
    enrich_stats = await run_enrich(session, only_pending=True)
    generate_stats = await run_generate_all(session)
    return JSONResponse(
        {
            "discover": discover_stats.to_dict(),
            "enrich": enrich_stats,
            "generate": generate_stats,
        }
    )


@app.post("/ui/leads/{lead_id}/generate")
async def ui_lead_generate(
    lead_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    lead = await _get_lead_or_404(session, lead_id)
    await generate_for_lead(session, lead)
    await session.commit()
    return _redirect_home(request)


@app.post("/ui/leads/{lead_id}/discard")
async def ui_lead_discard(
    lead_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    lead = await _get_lead_or_404(session, lead_id)
    lead.status = LeadStatus.DISCARDED
    await session.commit()
    return _redirect_home(request)


@app.post("/ui/leads/{lead_id}/mark-sent")
async def ui_lead_mark_sent(
    lead_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    lead = await _get_lead_or_404(session, lead_id)
    now = datetime.now(timezone.utc)
    for m in lead.messages or []:
        if not m.sent:
            m.sent = True
            m.sent_at = now
    if lead.status in (LeadStatus.NEW, LeadStatus.QUALIFIED):
        lead.status = LeadStatus.CONTACTED
        try:
            await schedule_followups_for_lead(session, lead)
        except Exception:
            logger.exception("No se pudieron programar followups para %s", lead.name)
    await session.commit()
    return _redirect_home(request)


# ----- UI forms para notas, tags, classify-reply -----

@app.post("/ui/leads/{lead_id}/notes")
async def ui_lead_add_note(
    lead_id: int,
    request: Request,
    body: str = "",
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Recibe form-encoded body=<texto>. Si vacio, no crea nada."""
    body = (body or "").strip()
    if body:
        lead = await _get_lead_or_404(session, lead_id)
        session.add(LeadNote(lead_id=lead.id, body=body[:10_000]))
        await session.commit()
    return _redirect_home(request)


@app.post("/ui/leads/{lead_id}/tags")
async def ui_lead_add_tag(
    lead_id: int,
    request: Request,
    tag: str = "",
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    norm = _normalize_tag(tag or "")
    if norm:
        lead = await _get_lead_or_404(session, lead_id)
        exists = await session.execute(
            select(LeadTag).where(LeadTag.lead_id == lead.id, LeadTag.tag == norm)
        )
        if not exists.scalar_one_or_none():
            session.add(LeadTag(lead_id=lead.id, tag=norm))
            await session.commit()
    return _redirect_home(request)


@app.post("/ui/leads/{lead_id}/tags/{tag}/remove")
async def ui_lead_remove_tag(
    lead_id: int,
    tag: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    norm = _normalize_tag(tag)
    result = await session.execute(
        select(LeadTag).where(LeadTag.lead_id == lead_id, LeadTag.tag == norm)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.commit()
    return _redirect_home(request)


@app.post("/ui/leads/{lead_id}/classify-reply")
async def ui_lead_classify_reply(
    lead_id: int,
    request: Request,
    raw_reply: str = "",
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Recibe form-encoded raw_reply=<texto del cliente>, clasifica y persiste."""
    raw_reply = (raw_reply or "").strip()
    if not raw_reply:
        return _redirect_home(request)
    # Reutiliza el endpoint API
    try:
        await api_lead_classify_reply(
            lead_id, ClassifyReplyIn(raw_reply=raw_reply), session
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Falla en ui_lead_classify_reply")
    return _redirect_home(request)


@app.post("/ui/jobs/followups")
async def ui_jobs_followups(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await run_pending_followups(session)
    return _redirect_home(request)


@app.post("/ui/llm/suggest-queries", response_class=HTMLResponse)
async def ui_llm_suggest_queries(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Devuelve un fragmento HTML (HTMX) con sugerencias del LLM."""
    from app.jobs.discover import _load_queries

    _, existing = _load_queries()
    existing_texts = [q.text for q in existing]
    db_rows = await session.execute(
        select(Lead.search_query).where(Lead.search_query.is_not(None)).distinct()
    )
    existing_texts.extend([r[0] for r in db_rows.all() if r[0]])

    client = get_llm_client()
    try:
        suggested = await client.suggest_queries(
            country="AR",
            focus="PyMEs argentinas con baja madurez digital",
            existing_queries=existing_texts,
            count=10,
        )
    except Exception as exc:
        logger.exception("suggest_queries fallo")
        return HTMLResponse(
            f'<div class="bg-rose-50 border border-rose-200 rounded p-3 text-sm text-rose-700">'
            f'Error al pedir sugerencias: {exc!s}</div>'
        )

    if not suggested:
        return HTMLResponse(
            '<div class="bg-slate-50 border border-slate-200 rounded p-3 text-sm text-slate-500">'
            'El LLM no devolvio sugerencias.</div>'
        )

    rows_html = []
    for s in suggested:
        prov = s.province or ""
        rows_html.append(f"""
        <li class="flex items-start gap-2 p-2 border-b border-purple-100 last:border-b-0">
          <div class="flex-1 min-w-0">
            <div class="text-sm font-medium text-slate-800">
              {s.rubro} · {s.city}{' · ' + prov if prov else ''}
            </div>
            <div class="text-xs text-slate-500 mt-0.5">{s.reason}</div>
          </div>
          <form method="post" action="/ui/llm/add-suggested-query" class="inline">
            <input type="hidden" name="rubro" value="{s.rubro}" />
            <input type="hidden" name="city" value="{s.city}" />
            <input type="hidden" name="province" value="{prov}" />
            <button class="text-xs px-2 py-1 rounded bg-purple-700 text-white hover:bg-purple-600 whitespace-nowrap"
                    title="Agrega esta query a data/search_queries.yml">
              Agregar
            </button>
          </form>
        </li>""")

    body = f"""
    <div class="bg-white border border-purple-200 rounded-lg p-3 shadow-sm">
      <div class="flex items-center justify-between mb-2">
        <span class="text-sm font-semibold text-purple-800">
          {len(suggested)} sugerencias del LLM
        </span>
        <button class="text-xs text-slate-400 hover:text-slate-700"
                onclick="document.getElementById('suggest-queries-target').innerHTML=''">
          cerrar
        </button>
      </div>
      <ul class="divide-y divide-purple-50">
        {''.join(rows_html)}
      </ul>
      <div class="text-[11px] text-slate-500 mt-2">
        Clickeá "Agregar" para sumarlas a <code>data/search_queries.yml</code>.
        Después usá "Pipeline completo" para buscar leads de esas queries.
      </div>
    </div>"""
    return HTMLResponse(body)


@app.post("/ui/llm/add-suggested-query")
async def ui_llm_add_suggested_query(
    request: Request,
    rubro: str = "",
    city: str = "",
    province: str = "",
) -> RedirectResponse:
    if rubro and city:
        _append_query_to_yaml(rubro, city, province or None)
    return _redirect_home(request)


# ----- Stats / Dashboard -----

async def _build_stats(session: AsyncSession) -> dict:
    """Snapshot del pipeline comercial. Lo consume /api/stats y /stats."""
    total_leads = (await session.execute(select(func.count(Lead.id)))).scalar() or 0

    # Leads por estado
    by_status_rows = await session.execute(
        select(Lead.status, func.count(Lead.id)).group_by(Lead.status)
    )
    by_status = {s.value: c for s, c in by_status_rows.all()}

    # Leads calificados sin contactar
    qualified_ready = (
        await session.execute(
            select(func.count(Lead.id)).where(
                Lead.status == LeadStatus.QUALIFIED,
                Lead.qualified.is_(True),
            )
        )
    ).scalar() or 0

    # Leads contactados
    contacted = by_status.get(LeadStatus.CONTACTED.value, 0)
    replied = by_status.get(LeadStatus.REPLIED.value, 0)
    discarded = by_status.get(LeadStatus.DISCARDED.value, 0)

    # Conversion rate (replied / contacted)
    reach_rate = (
        round((replied / contacted) * 100, 1) if contacted else 0.0
    )

    # Leads con contacto (telefono o email)
    leads_with_contact = (
        await session.execute(
            select(func.count(Lead.id)).where(
                or_safe := (Lead.phone.is_not(None) | Lead.email.is_not(None))
            )
        )
    ).scalar() or 0

    # Leads con email extraido del HTML
    leads_with_email = (
        await session.execute(
            select(func.count(Lead.id)).where(Lead.email.is_not(None))
        )
    ).scalar() or 0

    # Mensajes generados
    total_messages = (await session.execute(select(func.count(Message.id)))).scalar() or 0
    sent_messages = (
        await session.execute(select(func.count(Message.id)).where(Message.sent.is_(True)))
    ).scalar() or 0

    # Follow-ups
    pending_followups = (
        await session.execute(
            select(func.count(FollowUpTask.id)).where(
                FollowUpTask.status == FollowUpStatus.PENDING
            )
        )
    ).scalar() or 0
    due_followups = (
        await session.execute(
            select(func.count(FollowUpTask.id)).where(
                FollowUpTask.status == FollowUpStatus.PENDING,
                FollowUpTask.scheduled_for <= datetime.now(timezone.utc),
            )
        )
    ).scalar() or 0

    # Por ciudad y rubro (top 8)
    by_city_rows = await session.execute(
        select(Lead.city, func.count(Lead.id))
        .where(Lead.city.is_not(None))
        .group_by(Lead.city)
        .order_by(func.count(Lead.id).desc())
        .limit(8)
    )
    by_city = [{"city": c, "count": n} for c, n in by_city_rows.all()]

    by_cat_rows = await session.execute(
        select(Lead.category, func.count(Lead.id))
        .where(Lead.category.is_not(None))
        .group_by(Lead.category)
        .order_by(func.count(Lead.id).desc())
        .limit(8)
    )
    by_category = [{"category": c, "count": n} for c, n in by_cat_rows.all()]

    # Distribucion de scores
    score_buckets_rows = await session.execute(
        select(
            func.coalesce(Lead.priority_score, 0).label("score"),
            func.count(Lead.id),
        )
        .where(Lead.priority_score.is_not(None))
        .group_by(Lead.priority_score)
        .order_by(Lead.priority_score.desc())
    )
    by_score = [{"score": int(s), "count": int(n)} for s, n in score_buckets_rows.all()]

    # Top reply intents
    by_intent_rows = await session.execute(
        select(ReplyClassification.intent, func.count(ReplyClassification.id))
        .group_by(ReplyClassification.intent)
    )
    by_intent = [{"intent": i.value, "count": int(n)} for i, n in by_intent_rows.all()]

    return {
        "total_leads": int(total_leads),
        "by_status": by_status,
        "qualified_ready": int(qualified_ready),
        "contacted": int(contacted),
        "replied": int(replied),
        "discarded": int(discarded),
        "reach_rate_pct": reach_rate,
        "leads_with_contact": int(leads_with_contact),
        "leads_with_email": int(leads_with_email),
        "total_messages": int(total_messages),
        "sent_messages": int(sent_messages),
        "pending_followups": int(pending_followups),
        "due_followups": int(due_followups),
        "by_city": by_city,
        "by_category": by_category,
        "by_score": by_score,
        "by_intent": by_intent,
    }


@app.get("/api/stats")
async def api_stats(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    return JSONResponse(await _build_stats(session))


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    settings = get_settings()
    stats = await _build_stats(session)
    return templates.TemplateResponse(
        request=request,
        name="stats.html",
        context={"stats": stats, "mock": settings.mock_scraper, "mock_llm": settings.mock_llm},
    )


# ----- API: catalogo / RAG -----

@app.get("/api/catalog")
async def api_catalog(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    items = await list_all_catalog(session)
    return JSONResponse(
        [
            {
                "id": i.id,
                "slug": i.slug,
                "name": i.name,
                "category": i.category,
                "short_description": i.short_description,
                "target_audience": i.target_audience,
                "price_range": i.price_range,
            }
            for i in items
        ]
    )


@app.get("/api/rag/search")
async def api_rag_search(
    q: str = Query("", description="Consulta libre, ej: 'panaderia sin web en Cordoba'"),
    limit: int = Query(5, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    hits = await search_catalog(session, q, limit=limit)
    return JSONResponse(
        [
            {
                "id": h.id,
                "slug": h.slug,
                "name": h.name,
                "category": h.category,
                "short_description": h.short_description,
                "score": h.score,
            }
            for h in hits
        ]
    )


# ----- API: generacion de mensajes -----

class LeadContextIn(BaseModel):
    name: str
    category: str | None = None
    city: str | None = None
    province: str | None = None
    country: str = "AR"
    has_website: bool = False
    website: str | None = None
    website_status: str | None = None
    rating: float | None = None
    qualification_reason: str | None = None


class GenerateMessageIn(BaseModel):
    lead: LeadContextIn
    channel: Channel = "whatsapp"
    catalog_slugs: list[str] | None = Field(
        default=None,
        description=(
            "Opcional: lista de slugs del catalogo a inyectar en el prompt. "
            "Si se omite, se buscan automaticamente los mas relevantes."
        ),
    )


@app.post("/api/generate-message")
async def api_generate_message(
    payload: GenerateMessageIn,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    # Resolver catalogo a inyectar en el prompt
    if payload.catalog_slugs:
        all_items = await list_all_catalog(session)
        items_by_slug = {i.slug: i for i in all_items}
        selected = [items_by_slug[s] for s in payload.catalog_slugs if s in items_by_slug]
    else:
        # Heuristica: buscar por nombre + categoria + razon de calificacion
        q_parts = [
            payload.lead.name,
            payload.lead.category or "",
            payload.lead.city or "",
            payload.lead.qualification_reason or "",
            "sin web" if not payload.lead.has_website else "",
        ]
        hits = await search_catalog(session, " ".join(q_parts), limit=4)
        all_items = await list_all_catalog(session)
        items_by_id = {i.id: i for i in all_items}
        selected = [items_by_id[h.id] for h in hits if h.id in items_by_id]
        if not selected:
            selected = all_items[:3]

    catalog_services = [
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

    lead_ctx = LeadContext(
        name=payload.lead.name,
        category=payload.lead.category,
        city=payload.lead.city,
        province=payload.lead.province,
        country=payload.lead.country,
        has_website=payload.lead.has_website,
        website=payload.lead.website,
        website_status=payload.lead.website_status,
        rating=payload.lead.rating,
        qualification_reason=payload.lead.qualification_reason,
    )

    client = get_llm_client()
    msg = await client.generate_message(lead_ctx, payload.channel, catalog_services)

    return JSONResponse(
        {
            "channel": msg.channel,
            "subject": msg.subject,
            "body": msg.body,
            "model": msg.model,
            "prompt_version": msg.prompt_version,
            "catalog_used": [s.slug for s in catalog_services],
        }
    )
