"""Interfaz comun para clientes LLM (mock + Groq).

El switch lo hace `app.llm.factory.get_llm_client()` en base a `settings.mock_llm`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


Channel = Literal["whatsapp", "email"]


@dataclass(slots=True)
class LeadContext:
    """Subset de info de un lead que el LLM necesita para escribir el mensaje."""

    name: str
    category: str | None = None
    city: str | None = None
    province: str | None = None
    country: str = "AR"
    has_website: bool = False
    website: str | None = None
    website_status: str | None = None  # ej: "sin-web", "wix-viejo", "ssl-roto"
    rating: float | None = None
    qualification_reason: str | None = None


@dataclass(slots=True)
class CatalogService:
    slug: str
    name: str
    short_description: str
    long_description: str | None = None
    target_audience: str | None = None
    price_range: str | None = None


@dataclass(slots=True)
class GeneratedMessage:
    channel: Channel
    body: str
    subject: str | None  # solo para email
    model: str
    prompt_version: str


class LLMClient(Protocol):
    async def generate_message(
        self,
        lead: LeadContext,
        channel: Channel,
        catalog: list[CatalogService],
    ) -> GeneratedMessage: ...
