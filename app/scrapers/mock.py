"""Provider mockeado: lee fixtures de data/fixtures/google_maps_sample.json.

El JSON tiene la forma:
    {
        "queries": [
            {
                "rubro": "panaderia",
                "city": "Cordoba",
                "results": [
                    {"name": "...", "phone": "...", "website": null, ...}
                ]
            },
            ...
        ]
    }
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.scrapers.base import LeadProvider, ScrapedLead, SearchQuery


logger = logging.getLogger(__name__)


def _fixture_path() -> Path:
    return get_settings().data_dir / "fixtures" / "google_maps_sample.json"


@lru_cache(maxsize=1)
def _load_fixtures() -> dict[tuple[str, str], list[dict]]:
    path = _fixture_path()
    if not path.exists():
        logger.warning("Fixture %s no existe.", path)
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], list[dict]] = {}
    for q in data.get("queries", []):
        key = (q["rubro"].lower(), q["city"].lower())
        out[key] = q.get("results", [])
    return out


class MockLeadProvider:
    name = "mock"

    async def search(
        self,
        query: SearchQuery,
        *,
        max_results: int = 20,
    ) -> list[ScrapedLead]:
        fixtures = _load_fixtures()
        key = (query.rubro.lower(), query.city.lower())
        raw = fixtures.get(key, [])
        if not raw:
            # Fallback: si no matchea exacto, devuelve el primer set disponible
            # para que en dev siempre haya algo que ver.
            if fixtures:
                raw = next(iter(fixtures.values()))
                logger.info(
                    "Fixture sin match exacto para %s -> usando primer set disponible.",
                    key,
                )

        results: list[ScrapedLead] = []
        for r in raw[:max_results]:
            results.append(
                ScrapedLead(
                    name=r["name"],
                    category=r.get("category") or query.rubro,
                    city=r.get("city") or query.city,
                    province=r.get("province") or query.province,
                    country=r.get("country") or query.country,
                    phone=r.get("phone"),
                    email=r.get("email"),
                    website=r.get("website"),
                    address=r.get("address"),
                    rating=r.get("rating"),
                    reviews_count=r.get("reviews_count"),
                    source="mock",
                    source_id=r.get("source_id") or f"mock:{r['name']}",
                    search_query=query.text,
                )
            )
        return results
