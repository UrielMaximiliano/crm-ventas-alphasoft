"""LLM mock: genera respuestas deterministas basadas en el lead y el canal.

Util para desarrollo offline y CI sin gastar tokens reales de Groq.
Genera mensajes "creibles" pero no inteligentes - no hacen ningun matching
real con el catalogo. Activar con MOCK_LLM=1.
"""
from __future__ import annotations

import textwrap

from app.llm.base import (
    CatalogService,
    Channel,
    FollowUpMessage,
    GeneratedMessage,
    LeadContext,
    LeadIntel,
    ReplyAnalysis,
    SuggestedQuery,
)
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

    async def suggest_queries(
        self,
        *,
        country: str = "AR",
        focus: str = "PyMEs con baja madurez digital",
        existing_queries: list[str] | None = None,
        count: int = 10,
    ) -> list[SuggestedQuery]:
        # Pool de combinaciones tipicas (sin tocar las existentes)
        pool = [
            ("plomero", "Cordoba", "Cordoba"),
            ("electricista", "Rosario", "Santa Fe"),
            ("ortodoncista", "Mendoza", "Mendoza"),
            ("contador", "La Plata", "Buenos Aires"),
            ("escribano", "Mar del Plata", "Buenos Aires"),
            ("inmobiliaria", "Salta", "Salta"),
            ("carpinteria", "Neuquen", "Neuquen"),
            ("vinoteca", "Mendoza", "Mendoza"),
            ("estudio fotografico", "Cordoba", "Cordoba"),
            ("autoescuela", "Rosario", "Santa Fe"),
            ("clinica veterinaria", "Cordoba", "Cordoba"),
            ("nutricionista", "Tucuman", "Tucuman"),
        ]
        existing_set = set((q or "").lower() for q in (existing_queries or []))
        results: list[SuggestedQuery] = []
        for rubro, city, province in pool:
            text = f"{rubro} en {city} {country}".lower()
            if text in existing_set:
                continue
            results.append(
                SuggestedQuery(
                    rubro=rubro,
                    city=city,
                    province=province,
                    reason=f"Rubro {rubro} en {city}: tipico negocio sin sitio propio.",
                )
            )
            if len(results) >= count:
                break
        return results

    async def classify_reply(
        self,
        lead: LeadContext,
        raw_reply: str,
        catalog: list[CatalogService],
        *,
        previous_messages: list[GeneratedMessage] | None = None,
    ) -> ReplyAnalysis:
        import re as _re
        text = raw_reply.lower()

        def _has(*words: str) -> bool:
            """Word-boundary match para evitar falsos positivos
            (ej: 'equivocaron' no debe matchear 'caro')."""
            for w in words:
                if _re.search(rf"\b{_re.escape(w)}\b", text):
                    return True
            return False

        if _has("no me interesa", "no gracias", "no por ahora", "no necesito"):
            intent, sent = "not_interested", "negative"
            action = "Cerrar con cordialidad. No insistir."
            reply = (
                f"Gracias por responder! Cualquier cosa quedo a las ordenes. "
                f"Exitos con {lead.name}."
            )
        elif _has("no soy", "equivocaron", "no es aca", "equivocaste", "no es para mi"):
            intent, sent = "wrong_contact", "neutral"
            action = "Persona equivocada. Descartar lead o reenviar a quien corresponda."
            reply = (
                "Disculpa la molestia! Si conoces quien lleva la parte digital de "
                f"{lead.name} te agradezco si me podes pasar el contacto. "
            )
        elif _has("caro", "presupuesto", "costo", "precio", "cuanto sale"):
            intent, sent = "pricing_objection", "neutral"
            action = (
                "Cliente plantea objecion de precio. Ofrecer rango orientativo "
                "(USD 400-800 para landing) y opcion de empezar minimo viable."
            )
            reply = (
                "Bueno, depende del alcance. Una landing simple va USD 400-800 y "
                "la entregamos en 1-2 semanas. Si queres te paso un par de ejemplos "
                "y armamos algo que se ajuste a tu presupuesto."
            )
        elif _has("reunion", "llamada", "zoom", "meet", "cuando podemos"):
            intent, sent = "ask_meeting", "positive"
            action = "Cliente quiere agendar. Mandar disponibilidad o link de Calendly."
            reply = (
                "Buenisimo! Te paso 3 horarios para esta semana: martes 10am, "
                "miercoles 4pm, jueves 11am. Cual te queda mejor?"
            )
        elif _has("ejemplo", "ejemplos", "casos", "portfolio", "info"):
            intent, sent = "ask_info", "positive"
            action = "Cliente quiere ver ejemplos. Mandar 2-3 casos parecidos."
            reply = (
                "Genial, te paso 3 ejemplos parecidos al tuyo por mail. Te interesa "
                "que despues te tire numeros y tiempos para tu caso?"
            )
        else:
            intent, sent = "interested", "neutral"
            action = "Cliente respondio pero no es claro. Pedir mas detalle."
            reply = (
                f"Buenas! Antes de tirarte una propuesta, contame brevemente que "
                f"necesitas para {lead.name} (sitio nuevo, renovar uno actual, "
                "automatizar algo?). Asi te oriento bien."
            )

        return ReplyAnalysis(
            intent=intent,
            sentiment=sent,
            summary=raw_reply.strip()[:80],
            suggested_action=action,
            suggested_reply=reply,
            model="mock",
            prompt_version=PROMPT_VERSION,
        )

    async def generate_followup(
        self,
        lead: LeadContext,
        channel: Channel,
        catalog: list[CatalogService],
        *,
        previous_messages: list[GeneratedMessage] | None = None,
        days_since_last_contact: int = 5,
    ) -> FollowUpMessage:
        # Angulos rotativos segun cantidad de followups previos
        followups_count = sum(
            1 for m in (previous_messages or []) if m.prompt_version and "follow" in m.prompt_version.lower()
        )
        angles = ["social_proof", "pregunta_directa", "insight_rubro"]
        angle = angles[followups_count % len(angles)]

        rubro_label = (lead.category or "rubro").lower()
        if channel == "whatsapp":
            if angle == "social_proof":
                body = (
                    f"Hola {lead.name}, vuelvo a escribir. Hicimos hace poco una "
                    f"landing para otro {rubro_label} en {lead.city or 'AR'} y "
                    "duplicaron los contactos en 1 mes. Te interesa que te pase el caso?"
                )
            elif angle == "pregunta_directa":
                body = (
                    f"Hola {lead.name}, una pregunta corta: tenes pensado renovar "
                    "el sitio o sumar reservas online este trimestre?"
                )
            else:
                body = (
                    f"Hola {lead.name}, viendo {rubro_label} en {lead.city or 'AR'} "
                    "muchos negocios estan mejorando SEO local para captar busquedas "
                    "tipo 'rubro cerca de mi'. Te interesa que te muestre como?"
                )
            subject = None
        else:  # email
            if angle == "social_proof":
                subject = f"Caso parecido a {lead.name}"
                body = (
                    f"Asunto: {subject}\n\n"
                    "Hola,\n\n"
                    f"Te escribo de Alphasoft. Hace poco trabajamos con un {rubro_label} "
                    f"en {lead.city or 'Argentina'} y los resultados fueron concretos: "
                    "el sitio paso de 2 contactos por mes a 8 en 60 dias.\n\n"
                    f"Si te queres ahorrar la conversacion: te puedo armar una propuesta "
                    f"corta para {lead.name} en 2-3 dias. Avisame y la mando.\n\n"
                    "Saludos,\nUriel - Alphasoft\nalphasoftwebs@gmail.com"
                )
            elif angle == "pregunta_directa":
                subject = f"Una pregunta sobre {lead.name}"
                body = (
                    f"Asunto: {subject}\n\n"
                    "Hola,\n\n"
                    f"Una sola pregunta: tenes pensado renovar la presencia web de "
                    f"{lead.name} este trimestre, o esta para mas adelante?\n\n"
                    "Sea cual sea la respuesta te puedo armar un plan. Si no, no insisto.\n\n"
                    "Saludos,\nUriel - Alphasoft"
                )
            else:
                subject = f"Insight para {rubro_label} en {lead.city or 'AR'}"
                body = (
                    f"Asunto: {subject}\n\n"
                    "Hola,\n\n"
                    f"Mirando otros {rubro_label} en {lead.city or 'Argentina'}, la "
                    "mayoria pierde clientes por sitios viejos que no posicionan en "
                    "Google. Es la palanca mas barata para mover ventas.\n\n"
                    f"Si queres profundizamos para {lead.name}, te mando un breve "
                    "diagnostico gratis.\n\n"
                    "Saludos,\nUriel - Alphasoft\nalphasoftwebs@gmail.com"
                )

        return FollowUpMessage(
            body=body,
            channel=channel,
            subject=subject,
            angle=angle,
            model="mock",
            prompt_version=f"followup-{PROMPT_VERSION}",
        )
