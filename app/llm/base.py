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
    # Intel del LLM (opcional, agregado en enrich.analyze_lead)
    site_analysis: str | None = None
    pain_points: str | None = None  # separados por " | "
    recommended_service: str | None = None


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


@dataclass(slots=True)
class LeadIntel:
    """Analisis profundo de un lead generado por el LLM.

    Combina tres utilidades en una sola llamada al LLM para ahorrar tokens:
    - Analisis del sitio del negocio (que tiene mal, oportunidades concretas)
    - Scoring 1-10 de prioridad comercial
    - Extraccion ofuscada de emails/telefonos del HTML
    """

    priority_score: int  # 1-10
    priority_reason: str  # 1 linea de por que ese score
    site_analysis: str  # 2-3 frases sobre el estado del sitio
    pain_points: list[str]  # dolores concretos detectados
    recommended_service: str  # slug del catalogo que mejor calza
    extracted_emails: list[str]  # emails que no encontro el regex (ofuscados, etc)
    extracted_phones: list[str]  # telefonos extra
    model: str
    prompt_version: str


class LLMClient(Protocol):
    async def generate_message(
        self,
        lead: LeadContext,
        channel: Channel,
        catalog: list[CatalogService],
    ) -> GeneratedMessage: ...

    async def analyze_lead(
        self,
        lead: LeadContext,
        catalog: list[CatalogService],
        *,
        site_html_excerpt: str = "",
        website_status: str = "",
    ) -> LeadIntel: ...
