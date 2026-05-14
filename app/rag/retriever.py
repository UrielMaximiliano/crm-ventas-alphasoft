"""Recupera items del catalogo relevantes para una query.

Por ahora usa keyword matching simple sobre name/category/description.
La columna `embedding` esta lista en la tabla pero no se popula -
para escalar a >50 items se puede agregar sentence-transformers y
similarity search con pgvector.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CatalogItem


# Palabras "ruido" que no aportan al matching
_STOPWORDS = {
    "de", "la", "el", "en", "y", "para", "con", "los", "las", "un", "una",
    "del", "al", "que", "como", "por", "su", "sin", "este", "esta",
    "a", "o", "u", "es", "se", "lo",
}


def _tokens(text: str) -> set[str]:
    text = text.lower()
    text = re.sub(r"[^\w\sñáéíóúü]", " ", text)
    return {t for t in text.split() if len(t) > 2 and t not in _STOPWORDS}


@dataclass
class CatalogHit:
    id: int
    slug: str
    name: str
    category: str | None
    short_description: str
    score: float


async def search_catalog(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 5,
) -> list[CatalogHit]:
    """Busca items relevantes por overlap de tokens. Si la query esta vacia,
    devuelve los primeros `limit` items por id ascendente."""
    result = await session.execute(select(CatalogItem).order_by(CatalogItem.id))
    items = list(result.scalars().all())

    if not items:
        return []

    if not query or not query.strip():
        return [
            CatalogHit(
                id=i.id,
                slug=i.slug,
                name=i.name,
                category=i.category,
                short_description=i.short_description,
                score=0.0,
            )
            for i in items[:limit]
        ]

    q_tokens = _tokens(query)
    if not q_tokens:
        return [
            CatalogHit(
                id=i.id, slug=i.slug, name=i.name, category=i.category,
                short_description=i.short_description, score=0.0,
            )
            for i in items[:limit]
        ]

    scored: list[CatalogHit] = []
    for item in items:
        haystack = " ".join(
            filter(
                None,
                [
                    item.name,
                    item.category,
                    item.short_description,
                    item.long_description,
                    item.target_audience,
                ],
            )
        )
        item_tokens = _tokens(haystack)
        if not item_tokens:
            score = 0.0
        else:
            overlap = len(q_tokens & item_tokens)
            score = overlap / (len(q_tokens) ** 0.5)
        scored.append(
            CatalogHit(
                id=item.id,
                slug=item.slug,
                name=item.name,
                category=item.category,
                short_description=item.short_description,
                score=round(score, 4),
            )
        )

    scored.sort(key=lambda h: h.score, reverse=True)
    # Si todos los scores son 0, devolver al menos los primeros 3 como fallback
    if all(h.score == 0 for h in scored):
        return scored[:3]
    return [h for h in scored if h.score > 0][:limit]


async def list_all_catalog(session: AsyncSession) -> list[CatalogItem]:
    """Devuelve todos los items - util cuando se inyectan inline en un prompt LLM."""
    result = await session.execute(select(CatalogItem).order_by(CatalogItem.id))
    return list(result.scalars().all())
