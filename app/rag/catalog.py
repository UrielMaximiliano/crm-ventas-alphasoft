"""Carga el catalogo de servicios de Alphasoft (data/alphasoft_catalog.yml) a la DB.

Idempotente: si el hash del archivo no cambio respecto al ultimo seed, no toca nada.
Por ahora NO genera embeddings - eso queda para una fase opcional posterior si
crece el catalogo. La columna `embedding` queda NULL.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import CatalogItem


logger = logging.getLogger(__name__)


def _catalog_path() -> Path:
    return get_settings().data_dir / "alphasoft_catalog.yml"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _load_yaml(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "items" not in data:
        raise ValueError(f"YAML invalido en {path}: falta clave 'items'")
    return data["items"]


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.split())


async def seed_catalog(session: AsyncSession) -> dict[str, int]:
    """Sincroniza la DB con el YAML. Devuelve contadores."""
    path = _catalog_path()
    if not path.exists():
        logger.warning("Catalogo no encontrado en %s, salteo seed.", path)
        return {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0}

    file_hash = _file_hash(path)

    # Si todos los items existentes ya tienen este hash, no hay nada que hacer
    result = await session.execute(select(CatalogItem))
    existing = {row.slug: row for row in result.scalars().all()}
    if existing and all(row.source_hash == file_hash for row in existing.values()):
        logger.info("Catalogo sin cambios (hash=%s, %d items).", file_hash, len(existing))
        return {"created": 0, "updated": 0, "unchanged": len(existing), "deleted": 0}

    yaml_items = _load_yaml(path)
    yaml_slugs = {item["slug"] for item in yaml_items}

    created = updated = unchanged = 0
    for item in yaml_items:
        slug = item["slug"]
        norm = dict(
            name=_normalize(item.get("name")),
            category=_normalize(item.get("category")) or None,
            short_description=_normalize(item.get("short_description")),
            long_description=_normalize(item.get("long_description")) or None,
            target_audience=_normalize(item.get("target_audience")) or None,
            price_range=_normalize(item.get("price_range")) or None,
            source_hash=file_hash,
        )
        if slug in existing:
            row = existing[slug]
            changed = any(getattr(row, k) != v for k, v in norm.items())
            if changed:
                for k, v in norm.items():
                    setattr(row, k, v)
                updated += 1
            else:
                row.source_hash = file_hash
                unchanged += 1
        else:
            session.add(CatalogItem(slug=slug, **norm))
            created += 1

    deleted = 0
    for slug in set(existing) - yaml_slugs:
        await session.delete(existing[slug])
        deleted += 1

    await session.commit()
    logger.info(
        "Catalogo seed: created=%d updated=%d unchanged=%d deleted=%d (hash=%s)",
        created, updated, unchanged, deleted, file_hash,
    )
    return {"created": created, "updated": updated, "unchanged": unchanged, "deleted": deleted}
