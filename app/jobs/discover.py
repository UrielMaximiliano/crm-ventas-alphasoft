"""Job `discover`: lee data/search_queries.yml, llama al LeadProvider activo
y persiste los leads nuevos en la DB.

Dedup: (name, address_normalized) - mismo lead en re-runs solo actualiza last_scraped_at.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import JobRun, Lead, LeadStatus
from app.scrapers.base import LeadProvider, ScrapedLead, SearchQuery, normalize_address


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DiscoverStats:
    queries_run: int = 0
    leads_found: int = 0
    leads_new: int = 0
    leads_updated: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict:
        return {
            "queries_run": self.queries_run,
            "leads_found": self.leads_found,
            "leads_new": self.leads_new,
            "leads_updated": self.leads_updated,
            "errors": self.errors or [],
        }


def _queries_path() -> Path:
    return get_settings().data_dir / "search_queries.yml"


def _load_queries() -> tuple[dict, list[SearchQuery]]:
    path = _queries_path()
    if not path.exists():
        logger.warning("search_queries.yml no encontrado en %s.", path)
        return {}, []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {}) or {}
    queries = []
    for q in raw.get("queries", []) or []:
        queries.append(
            SearchQuery(
                rubro=q["rubro"],
                city=q["city"],
                province=q.get("province"),
                country=q.get("country") or defaults.get("country", "AR"),
            )
        )
    return defaults, queries


async def _upsert_lead(session: AsyncSession, raw: ScrapedLead) -> tuple[Lead, bool]:
    """Devuelve (lead, was_created)."""
    addr_norm = normalize_address(raw.address)
    result = await session.execute(
        select(Lead).where(
            Lead.name == raw.name,
            Lead.address_normalized == addr_norm,
        )
    )
    existing = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing is None:
        lead = Lead(
            name=raw.name,
            category=raw.category,
            city=raw.city,
            province=raw.province,
            country=raw.country,
            phone=raw.phone,
            email=raw.email,
            website=raw.website,
            address=raw.address,
            address_normalized=addr_norm,
            rating=raw.rating,
            reviews_count=raw.reviews_count,
            source=raw.source,
            source_id=raw.source_id,
            search_query=raw.search_query,
            status=LeadStatus.NEW,
            last_scraped_at=now,
        )
        session.add(lead)
        return lead, True

    # Update parcial: solo refresca datos que pueden haber cambiado
    existing.phone = raw.phone or existing.phone
    existing.email = raw.email or existing.email
    existing.website = raw.website or existing.website
    existing.rating = raw.rating if raw.rating is not None else existing.rating
    existing.reviews_count = (
        raw.reviews_count if raw.reviews_count is not None else existing.reviews_count
    )
    existing.search_query = raw.search_query or existing.search_query
    existing.last_scraped_at = now
    return existing, False


async def run_discover(
    session: AsyncSession,
    provider: LeadProvider,
    *,
    max_queries: int | None = None,
) -> DiscoverStats:
    defaults, queries = _load_queries()
    if max_queries:
        queries = queries[:max_queries]
    if not queries:
        logger.warning("Sin queries cargadas, salteo discover.")
        return DiscoverStats()

    job_run = JobRun(job_name="discover", started_at=datetime.now(timezone.utc))
    session.add(job_run)
    await session.flush()

    stats = DiscoverStats(errors=[])
    settings = get_settings()
    max_results = int(defaults.get("max_results", 20))
    daily_limit = max(0, settings.scraper_daily_limit)

    for q in queries:
        if daily_limit and stats.leads_found >= daily_limit:
            logger.info("Discover corta por SCRAPER_DAILY_LIMIT=%d", daily_limit)
            break

        stats.queries_run += 1
        try:
            query_limit = max_results
            if daily_limit:
                query_limit = min(query_limit, daily_limit - stats.leads_found)
            scraped = await provider.search(q, max_results=query_limit)
        except Exception as exc:
            msg = f"{q.text}: {exc!r}"
            logger.exception("Falla en query '%s'", q.text)
            stats.errors.append(msg)
            continue

        if daily_limit:
            scraped = scraped[: max(0, daily_limit - stats.leads_found)]
        stats.leads_found += len(scraped)
        for raw in scraped:
            _lead, created = await _upsert_lead(session, raw)
            if created:
                stats.leads_new += 1
            else:
                stats.leads_updated += 1

        await session.flush()

    job_run.finished_at = datetime.now(timezone.utc)
    job_run.success = not stats.errors
    job_run.items_processed = stats.leads_found
    if stats.errors:
        job_run.error = "\n".join(stats.errors)

    await session.commit()
    logger.info("Discover terminado: %s", stats.to_dict())
    return stats
