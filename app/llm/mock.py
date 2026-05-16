"""LLM mock: genera respuestas deterministas basadas en el lead y el canal.

Util para desarrollo offline y CI sin gastar tokens reales de Groq.
Genera mensajes "creibles" pero no inteligentes - no hacen ningun matching
real con el catalogo. Activar con MOCK_LLM=1.
"""
from __future__ import annotations

import textwrap

from app.llm.base import CatalogService, Channel, GeneratedMessage, LeadContext, LeadIntel
from app.llm.prompts import PROMPT_VERSION


class MockLLMClient:
    name = "mock"

    async def generate_message(
        self,
        lead: LeadContext,
        channel: Channel,
        catalog: list[CatalogService],
    ) -> GeneratedMessage:
        # Heuristica simple para "elegir" un servicio relevante
        if not lead.has_website:
            top = next(
                (s for s in catalog if s.slug == "landing-page-pyme"),
                catalog[0] if catalog else None,
            )
            pain = "vi que todavia no tienen sitio web"
        elif lead.website_status and "wix" in lead.website_status.lower():
            top = next(
                (s for s in catalog if s.slug == "migracion-wix-wordpress"),
                catalog[0] if catalog else None,
            )
            pain = "vi que estan en Wix y carga lento en celular"
        else:
            top = catalog[0] if catalog else None
            pain = "estuve mirando algunas mejoras para su web"

        servicio = top.name.lower() if top else "una landing rapida"
        where = f" en {lead.city}" if lead.city else ""

        if channel == "whatsapp":
            body = textwrap.dedent(f"""\
                Hola! Vi {lead.name}{where} en Google Maps y {pain}.
                Soy Uriel de Alphasoft, hacemos {servicio} para PyMEs argentinas.
                Te interesa que te pase un par de ejemplos parecidos y una idea de cuanto saldria?
            """).strip()
            return GeneratedMessage(
                channel="whatsapp",
                body=body,
                subject=None,
                model="mock",
                prompt_version=PROMPT_VERSION,
            )

        # email
        subject = f"Una propuesta rapida para {lead.name}"
        body_lines = [
            "Hola,",
            "",
            f"Soy Uriel de Alphasoft (alphasoft.cloud). Vi {lead.name} en Google Maps y {pain}.",
            "",
        ]
        if top:
            body_lines.extend(
                [
                    f"En Alphasoft hacemos {top.name.lower()}: {top.short_description.strip()}",
                    "",
                ]
            )
        body_lines.extend(
            [
                "Si te interesa, te puedo mandar 2-3 ejemplos parecidos y un rango de costos por mail.",
                "",
                "Saludos,",
                "Uriel - Alphasoft",
                "alphasoftwebs@gmail.com",
            ]
        )
        return GeneratedMessage(
            channel="email",
            body="\n".join(body_lines),
            subject=subject,
            model="mock",
            prompt_version=PROMPT_VERSION,
        )

    async def analyze_lead(
        self,
        lead: LeadContext,
        catalog: list[CatalogService],
        *,
        site_html_excerpt: str = "",
        website_status: str = "",
    ) -> LeadIntel:
        # Heuristica mock: scoring por presencia de "dolor" + reviews
        score = 5
        pain_points: list[str] = []
        reason = "sin senial clara"

        if not lead.has_website:
            score = 8
            pain_points.append("Sin sitio web propio")
            reason = "Sin web - oportunidad clara"
        elif "wix" in (website_status or "").lower():
            score = 8
            pain_points.append("Sitio en Wix con limitaciones de performance")
            reason = "Wix - migracion rentable"
        elif "link-social" in (website_status or "").lower():
            score = 7
            pain_points.append("Solo presencia en red social, sin sitio propio")
            reason = "Solo redes - falta de presencia web"
        elif "caido" in (website_status or "").lower():
            score = 9
            pain_points.append("Sitio caido - oportunidad inmediata")
            reason = "Sitio caido"
        elif "lento" in (website_status or "").lower():
            score = 6
            pain_points.append("Sitio carga lento, perjudica SEO y conversion")
            reason = "Sitio lento"
        elif "no-mobile" in (website_status or "").lower():
            score = 6
            pain_points.append("Sitio no mobile-friendly")
            reason = "No responsive"

        if lead.rating and lead.rating >= 4.5:
            score = min(10, score + 1)

        recommended = "landing-page-pyme"
        if "wix" in (website_status or "").lower() or "wordpress" in (website_status or "").lower():
            recommended = "migracion-wix-wordpress"
        elif "lento" in (website_status or "").lower():
            recommended = "seo-local-google"

        return LeadIntel(
            priority_score=score,
            priority_reason=reason,
            site_analysis=(
                f"Negocio con presencia digital de tipo '{website_status or 'desconocida'}'. "
                f"Rating {lead.rating or '-'}."
            ),
            pain_points=pain_points or ["Sin dolor evidente detectado"],
            recommended_service=recommended,
            extracted_emails=[],
            extracted_phones=[],
            model="mock",
            prompt_version=PROMPT_VERSION,
        )
