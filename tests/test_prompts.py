"""Tests de los prompt builders — verifican que el texto que va a Groq tenga
toda la informacion necesaria."""
from __future__ import annotations

from app.llm.base import CatalogService, LeadContext
from app.llm.prompts import (
    build_email_prompt,
    build_whatsapp_prompt,
    parse_email_response,
)


def test_whatsapp_prompt_incluye_datos_del_lead():
    lead = LeadContext(
        name="Panaderia La Espiga",
        category="Panaderia",
        city="Cordoba",
        province="Cordoba",
        has_website=False,
        qualification_reason="sin sitio web",
        rating=4.7,
    )
    cat = [CatalogService(slug="lp", name="Landing PyMEs", short_description="x")]
    p = build_whatsapp_prompt(lead, cat)
    assert "Panaderia La Espiga" in p
    assert "Cordoba" in p
    assert "sin sitio web" in p
    assert "4.7" in p
    assert "Landing PyMEs" in p
    assert "WhatsApp" in p
    assert "argentino" in p.lower() or "voseo" in p.lower()
    assert 'El nombre exacto es: "Panaderia La Espiga"' in p


def test_email_prompt_pide_asunto_y_cuerpo():
    lead = LeadContext(name="Foo", has_website=True, website="https://foo.com")
    p = build_email_prompt(lead, [])
    assert "Asunto:" in p
    assert "EMAIL" in p
    assert 'nombre exacto "Foo"' in p


def test_parse_email_response_extrae_asunto():
    raw = "Asunto: Hola desde Alphasoft\n\nHola,\nTexto del cuerpo."
    subject, body = parse_email_response(raw)
    assert subject == "Hola desde Alphasoft"
    assert body.startswith("Hola,")


def test_parse_email_response_sin_asunto():
    raw = "Hola, esto es solo cuerpo sin prefijo de asunto."
    subject, body = parse_email_response(raw)
    assert subject is None
    assert body.startswith("Hola")
