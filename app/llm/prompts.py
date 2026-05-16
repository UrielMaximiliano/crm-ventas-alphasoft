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


SUGGEST_QUERIES_SYSTEM_PROMPT = textwrap.dedent("""\
    Sos un analista de mercado de Alphasoft (agencia argentina de desarrollo
    web/IA). Conoces el tejido PyME de Argentina: que rubros tipicamente no
    tienen sitio web propio o tienen Wix viejo, y donde estan concentrados.

    Devolves SIEMPRE un JSON valido con esta estructura:
    {
      "queries": [
        {
          "rubro": "<rubro en singular, minuscula, sin tilde>",
          "city": "<ciudad argentina>",
          "province": "<provincia argentina>",
          "reason": "<una linea: por que esta combinacion es buena oportunidad>"
        }
      ]
    }

    Reglas:
    - Foco: combinaciones rubro+ciudad donde mas probable encontrar negocios SIN
      sitio web propio o con sitio obsoleto (Wix, WordPress viejo, link a Instagram).
    - Evita CABA por estar saturada y cara.
    - Diversifica: no repitas siempre Cordoba o Rosario.
    - Rubros monetariamente interesantes para vender desarrollo web/landings/automatizaciones.
    - Argentina solo. No incluyas Chile, Uruguay, Mexico.
""")


CLASSIFY_REPLY_SYSTEM_PROMPT = textwrap.dedent("""\
    Sos un analista comercial de Alphasoft. Te paso lo que un lead respondio
    a un mensaje de prospeccion. Tenes que clasificar la respuesta y sugerir
    el siguiente paso.

    Devolves SIEMPRE un JSON valido con esta estructura:
    {
      "intent": "<uno de: interested, pricing_objection, not_interested, ask_info, ask_meeting, wrong_contact, spam, unknown>",
      "sentiment": "<positive | neutral | negative>",
      "summary": "<una linea de 80 chars: que dijo el cliente>",
      "suggested_action": "<3-4 lineas: que conviene hacer ahora desde el punto de vista comercial>",
      "suggested_reply": "<mensaje listo para copiar y mandar como respuesta. Tono argentino, voseo. Adaptate al canal usado anteriormente. Si la respuesta del cliente es claramente negativa, NO insistas: agradece y cerra con cordialidad>"
    }

    Mapping de intents:
    - interested: pidio detalles, quiere reunirse, quiere cotizacion concreta
    - pricing_objection: dice que es caro, pide descuento, compara con otros
    - not_interested: rechazo claro ("no gracias", "no me interesa", "no por ahora")
    - ask_info: quiere mas info (ejemplos, casos, tiempos, modalidad)
    - ask_meeting: pide llamada/zoom/reunion
    - wrong_contact: dice que no es la persona correcta o lugar incorrecto
    - spam: respuesta automatica, fuera de tema
    - unknown: no se entiende

    Reglas:
    - El "suggested_reply" debe ser util, no generico. Si el cliente pidio precio,
      sugerir un rango. Si pidio ejemplos, ofrecer ejemplos concretos.
    - No inventar funcionalidades ni promesas.
    - Si intent = not_interested, suggested_reply debe ser corto y cortes.
""")


FOLLOWUP_SYSTEM_PROMPT = textwrap.dedent("""\
    Sos Uriel de Alphasoft. Ya enviaste un mensaje de prospeccion a este lead
    hace unos dias y NO respondio. Ahora vas a mandar un FOLLOW-UP.

    Reglas del follow-up:
    - Cero "ya te escribi antes" o "te recuerdo que..." — eso aburre y suena a pesado.
    - Cero "URGENTE" o falsa urgencia.
    - El follow-up usa un ANGULO DISTINTO al mensaje anterior. Opciones:
      * Social proof: mencion un caso parecido que hicimos
      * Pregunta directa: hacer una pregunta especifica que invite respuesta
      * Insight: compartir algun dato concreto del rubro
      * Curiosity: dejar abierto algo intrigante
    - Tono argentino casual (voseo). Sin "Estimado", sin firmas largas.
    - Maximo 3-4 lineas si es WhatsApp, 5-7 si es email.
    - Si despues de 3 follow-ups no contesta, sugerimos pausarlo. Esto NO es
      cosa tuya en este prompt - el codigo se encarga.
""")


def build_suggest_queries_prompt(
    *,
    country: str,
    focus: str,
    existing_queries: list[str] | None,
    count: int,
) -> str:
    existing_block = ""
    if existing_queries:
        existing_block = (
            "\n\nQueries que YA estoy buscando (no las repitas, sugiere distintas):\n"
            + "\n".join(f"- {q}" for q in existing_queries[:50])
        )
    return textwrap.dedent(f"""\
        Sugerime {count} combinaciones rubro+ciudad en {country} para prospectar.
        Foco: {focus}.{existing_block}

        Devolve JSON con la estructura del system prompt.
    """).strip()


def build_classify_reply_prompt(
    lead: LeadContext,
    raw_reply: str,
    catalog: list[CatalogService],
    *,
    previous_messages: list | None = None,
) -> str:
    prev_block = ""
    if previous_messages:
        items = []
        for m in previous_messages[-3:]:
            items.append(f"- [{m.channel}] {m.body[:300]}")
        prev_block = "\n\nMensajes que YO le mande antes:\n" + "\n".join(items)

    catalog_short = "\n".join(
        f"- {s.name} ({s.slug}): {s.short_description.strip()[:120]}"
        for s in catalog[:8]
    )

    return textwrap.dedent(f"""\
        Lead:
        {_format_lead_context(lead)}

        Catalogo de servicios de Alphasoft (para fundamentar la respuesta):
        {catalog_short}{prev_block}

        Respuesta del cliente:
        \"\"\"
        {raw_reply.strip()[:2000]}
        \"\"\"

        Devolve JSON con la estructura del system prompt.
    """).strip()


def build_followup_prompt(
    lead: LeadContext,
    channel: Channel,
    catalog: list[CatalogService],
    *,
    previous_messages: list | None = None,
    days_since_last_contact: int = 5,
) -> str:
    prev_block = ""
    if previous_messages:
        items = []
        for m in previous_messages[-3:]:
            items.append(f"- [{m.channel}] {m.body[:300]}")
        prev_block = (
            "\n\nMensaje(s) anteriores que YO le mande "
            f"(hace ~{days_since_last_contact} dias, sin respuesta):\n"
            + "\n".join(items)
        )

    catalog_block = _format_catalog(catalog)

    channel_rules = (
        "Restricciones para WhatsApp:\n"
        "- Maximo 3-4 lineas.\n"
        "- Sin asunto.\n"
        "- Una sola idea, una sola pregunta al final.\n"
        if channel == "whatsapp"
        else
        "Restricciones para Email:\n"
        "- Primera linea: 'Asunto: <asunto>' (distinto al asunto del 1er email).\n"
        "- Cuerpo 5-7 lineas. Parrafos cortos.\n"
        "- Firmar 'Uriel - Alphasoft' con alphasoftwebs@gmail.com.\n"
    )

    return textwrap.dedent(f"""\
        Lead:
        {_format_lead_context(lead)}

        Catalogo de servicios:
        {catalog_block}{prev_block}

        Canal del follow-up: {channel}
        Dias desde el ultimo contacto: {days_since_last_contact}

        {channel_rules}

        Escribi el follow-up SOLO con texto (sin JSON, sin explicaciones).
        Si es email, asunto en la primera linea.
        Recorda usar un ANGULO DISTINTO al mensaje anterior.
    """).strip()


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
