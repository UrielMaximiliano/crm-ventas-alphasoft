"""Interfaz comun para proveedores de leads.

`MockLeadProvider` lee fixtures locales.
`GoogleMapsLeadProvider` (Fase 8) scrapea Google Maps con Scrapling.
Cualquier otro (SerpAPI, Outscraper, Places API oficial) puede implementar
esta interfaz sin tocar el resto del codigo.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class ScrapedLead:
    name: str
    category: str | None = None
    city: str | None = None
    province: str | None = None
    country: str = "AR"
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    address: str | None = None
    rating: float | None = None
    reviews_count: int | None = None
    source: str = "mock"
    source_id: str | None = None
    search_query: str | None = None


@dataclass(slots=True)
class SearchQuery:
    rubro: str
    city: str
    province: str | None = None
    country: str = "AR"

    @property
    def text(self) -> str:
        parts = [self.rubro, "en", self.city]
        if self.country:
            parts.append(self.country)
        return " ".join(parts)


class LeadProvider(Protocol):
    name: str

    async def search(
        self,
        query: SearchQuery,
        *,
        max_results: int = 20,
    ) -> list[ScrapedLead]: ...


# ---- Helpers compartidos ----

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def normalize_address(value: str | None) -> str:
    """Normaliza una direccion para usar en dedup.

    - lowercases
    - quita tildes
    - quita caracteres no alfanumericos
    - colapsa espacios
    """
    if not value:
        return ""
    s = value.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = _NON_ALNUM.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s
