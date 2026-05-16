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
from app.db.models import Lead, LeadStatus, Message, MessageChannel
from app.db.session import get_session, session_scope
from app.exports import build_leads_filename, build_leads_xlsx
from app.jobs.discover import run_discover
from app.jobs.enrich import run_enrich
from app.jobs.generate import generate_for_lead, run_generate_all
from app.scheduler import build_scheduler, maybe_run_discover_at_startup
from app.llm.base import CatalogService, Channel, LeadContext
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
        .options(selectinload(Lead.messages))
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
        select(Lead).options(selectinload(Lead.messages)).where(Lead.id == lead_id)
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
    await session.commit()
    return _redirect_home(request)


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
