"""Plantillas de prompt para los mensajes de prospeccion.

Tono Alphasoft (segun el sitio): casual, accesible, "dos developers", orientado
a startups y PyMEs argentinas. NO usar marketing-speak (ni "transformamos tu
negocio" ni "soluciones a medida que llevan el exito al siguiente nivel").
"""
from __future__ import annotations

import textwrap

from app.llm.base import CatalogService, Channel, LeadContext


PROMPT_VERSION = "v2"


SYSTEM_PROMPT = textwrap.dedent("""\
    Sos Uriel, fundador de Alphasoft (alphasoft.cloud), una agencia argentina
    de desarrollo web y automatizacion con IA. Trabajan dos developers, hacen
    landings rapidas en Next.js, tiendas online, chatbots con IA y automatizaciones.
    Tono: casual y directo, escribis como argentino (voseo, "che" si va), sin sonar
    a marketing barato. Ayudas a PyMEs, comercios locales, profesionales independientes.

    Reglas:
    - Copia EXACTAMENTE el nombre del negocio como viene en el contexto. No lo
      corrijas, no lo traduzcas, no agregues ni cambies letras.
    - Trata el nombre del negocio como una marca: no inventes el genero de la
      persona, el dueno, la especialidad ni datos no incluidos en el contexto.
    - Nunca prometas resultados magicos ni inventes funcionalidades.
    - Si el lead no tiene web, mencionalo como observacion, no como sermon.
    - Si la web esta hecha en Wix/WordPress viejo, podes mencionarlo pero con respeto.
    - Cero clickbait ni urgencia falsa ("URGENTE", "ULTIMA OPORTUNIDAD" estan prohibidos).
    - Mensajes para Argentina: usa voseo, decis "boludo" jamas, evita anglicismos innecesarios.
    - No inventes datos del negocio que no esten en el contexto.
    - No firmes con "Saludos cordiales" - mas casual: "Saludos", "Un abrazo", o nada.
    """)


def _format_lead_context(lead: LeadContext) -> str:
    lines = [f"- Nombre del negocio: {lead.name}"]
    if lead.category:
        lines.append(f"- Rubro: {lead.category}")
    where_parts = [p for p in [lead.city, lead.province] if p]
    if where_parts:
        lines.append(f"- Ubicacion: {', '.join(where_parts)}, {lead.country}")
    if lead.rating is not None:
        lines.append(f"- Rating en Google: {lead.rating}")
    if lead.has_website:
        web_part = f"si ({lead.website})" if lead.website else "si"
        lines.append(f"- Tiene sitio web: {web_part}")
        if lead.website_status:
            lines.append(f"- Estado del sitio: {lead.website_status}")
    else:
        lines.append("- Tiene sitio web: NO")
    if lead.qualification_reason:
        lines.append(f"- Por que es lead calificado: {lead.qualification_reason}")
    # Intel del LLM si esta presente
    if getattr(lead, "site_analysis", None):
        lines.append(f"- Analisis del sitio: {lead.site_analysis}")
    if getattr(lead, "pain_points", None):
        pp = lead.pain_points if isinstance(lead.pain_points, str) else " | ".join(lead.pain_points)
        lines.append(f"- Dolores concretos detectados: {pp}")
    if getattr(lead, "recommended_service", None):
        lines.append(f"- Servicio recomendado (del catalogo): {lead.recommended_service}")
    return "\n".join(lines)


def _format_catalog(catalog: list[CatalogService]) -> str:
    if not catalog:
        return "(sin servicios cargados)"
    lines = []
    for s in catalog:
        lines.append(f"### {s.name} ({s.slug})")
        lines.append(s.short_description.strip())
        if s.target_audience:
            lines.append(f"Para: {s.target_audience}")
        if s.price_range:
            lines.append(f"Rango: {s.price_range}")
        lines.append("")
    return "\n".join(lines).strip()


def build_whatsapp_prompt(lead: LeadContext, catalog: list[CatalogService]) -> str:
    return textwrap.dedent(f"""\
        Escribi un mensaje de WhatsApp de PROSPECCION FRIA (primer contacto) para este lead.

        Lead:
        {_format_lead_context(lead)}

        Catalogo de servicios disponibles:
        {_format_catalog(catalog)}

        Restricciones del mensaje:
        - Maximo 4-5 lineas (cabe en una pantalla de celular sin scroll).
        - Empezar con un saludo corto y referir al negocio por nombre EXACTO.
        - El nombre exacto es: "{lead.name}". Debe aparecer igual, sin cambios.
        - Identificar UN solo dolor concreto (sin web, sitio viejo, etc.) sin sonar pedante.
        - Proponer UN solo servicio del catalogo, el mas relevante para este lead.
        - Cerrar con una pregunta corta que invite a responder ("Te paso un ejemplo?",
          "Te interesa que te tire numeros?", etc.).
        - NO incluyas tu numero ni el de Alphasoft (el destinatario ya esta en WhatsApp).
        - Tono argentino, voseo. Sin emojis salvo uno sutil al final si suma calidez.

        Devolve SOLO el texto del mensaje, sin explicaciones ni headers.
    """).strip()


def build_email_prompt(lead: LeadContext, catalog: list[CatalogService]) -> str:
    return textwrap.dedent(f"""\
        Escribi un EMAIL de PROSPECCION FRIA (primer contacto) para este lead.

        Lead:
        {_format_lead_context(lead)}

        Catalogo de servicios disponibles:
        {_format_catalog(catalog)}

        Restricciones del mensaje:
        - Primera linea: "Asunto: <asunto>" (asunto especifico, no generico).
        - Despues una linea en blanco y el cuerpo del email.
        - Cuerpo: maximo 6-8 lineas, parrafos cortos.
        - Usar el nombre exacto "{lead.name}" cuando nombres al negocio.
        - No cambies, corrijas ni completes el nombre del negocio.
        - Saludar por nombre del negocio o "Hola,".
        - Mencionar de donde sale el contacto ("Vi {lead.name} en Google Maps").
        - Identificar UN dolor concreto basado en los datos del lead.
        - Proponer UN solo servicio del catalogo (el mas relevante).
        - Incluir una mencion concreta de proximo paso (ej: "te puedo mandar 2-3 ejemplos
          parecidos por mail" o "te puedo llamar 10min").
        - Firmar con "Uriel - Alphasoft" y agregar el mail de contacto (alphasoftwebs@gmail.com).
        - Tono argentino pero un poco mas formal que WhatsApp.
        - NO uses "Estimado/a". Usa "Hola,".

        Devolve SOLO el email (asunto + cuerpo), sin explicaciones ni headers extras.
    """).strip()


def build_prompt(lead: LeadContext, channel: Channel, catalog: list[CatalogService]) -> str:
    if channel == "whatsapp":
        return build_whatsapp_prompt(lead, catalog)
    if channel == "email":
        return build_email_prompt(lead, catalog)
    raise ValueError(f"Canal desconocido: {channel}")


def parse_email_response(text: str) -> tuple[str | None, str]:
    """Extrae (asunto, cuerpo) de la respuesta del LLM para canal email."""
    text = text.strip()
    if text.lower().startswith("asunto:"):
        first_newline = text.find("\n")
        if first_newline == -1:
            return text[7:].strip(), ""
        subject = text[7:first_newline].strip()
        body = text[first_newline:].strip()
        return subject, body
    return None, text


ANALYZE_SYSTEM_PROMPT = textwrap.dedent("""\
    Sos un analista comercial de Alphasoft. Tu tarea es leer informacion de un
    lead (negocio argentino) y, si tenemos el HTML de su sitio, evaluar la
    oportunidad comercial concreta. Respondes SIEMPRE en JSON valido sin texto
    extra alrededor, con esta estructura exacta:

    {
      "priority_score": <int 1-10>,
      "priority_reason": "<una linea, 80 chars max>",
      "site_analysis": "<2-3 frases sobre el estado del sitio o presencia digital>",
      "pain_points": ["<dolor concreto>", "<otro dolor concreto>"],
      "recommended_service": "<slug exacto del catalogo>",
      "extracted_emails": ["<emails extra que veas en el HTML, incluso ofuscados>"],
      "extracted_phones": ["<telefonos extra que veas, formato libre>"]
    }

    Criterio de score (1-10):
    - 9-10: dolor evidente + negocio con clientes (>50 reseñas) + rubro de alto ticket
    - 7-8: dolor evidente + negocio mediano, o sin dolor pero rubro premium
    - 5-6: dolor mediano, conviene contactar pero sin urgencia
    - 3-4: sitio decente o negocio chico
    - 1-2: ya tiene un sitio en buen estado y modernidad, baja chance de cerrar

    Reglas estrictas:
    - "extracted_emails" / "extracted_phones": SOLO si los ves en el HTML.
      Incluye emails ofuscados ("info AT empresa DOT com" -> "info@empresa.com",
      "info [arroba] empresa" -> "info@empresa..."). NO inventes.
    - "recommended_service" debe ser un slug del catalogo que te pasamos.
    - "pain_points" maximo 3, en español, concretos y especificos al negocio.
    - Si no tenemos HTML del sitio, basate solo en metadata + rubro + ciudad.
    """)


def build_analyze_lead_prompt(
    lead: LeadContext,
    catalog: list[CatalogService],
    *,
    site_html_excerpt: str = "",
    website_status: str = "",
) -> str:
    catalog_slugs = [s.slug for s in catalog]
    html_block = ""
    if site_html_excerpt:
        # Truncar para no quemar tokens: solo el head + primeros 3000 chars del body
        excerpt = site_html_excerpt[:5000]
        html_block = f"\n\nHTML del sitio (primeros 5000 chars):\n```html\n{excerpt}\n```"
    elif website_status:
        html_block = f"\n\n(No tenemos HTML del sitio. Estado detectado: {website_status})"

    catalog_block = _format_catalog(catalog)

    return textwrap.dedent(f"""\
        Lead a analizar:
        {_format_lead_context(lead)}

        Catalogo de servicios de Alphasoft (slugs validos: {", ".join(catalog_slugs)}):
        {catalog_block}{html_block}

        Devolve UN JSON con la estructura del system prompt. Sin texto extra.
    """).strip()
