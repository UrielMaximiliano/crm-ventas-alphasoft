"""Tests para las 3 funciones LLM nuevas del workflow: suggest_queries,
classify_reply y generate_followup. Usan MockLLMClient (sin red ni tokens)."""
from __future__ import annotations

from app.llm.base import (
    CatalogService,
    GeneratedMessage,
    LeadContext,
)
from app.llm.mock import MockLLMClient


CATALOG = [
    CatalogService(
        slug="landing-page-pyme",
        name="Landing page para PyMEs",
        short_description="Sitio simple en Next.js para negocios sin web.",
    ),
    CatalogService(
        slug="migracion-wix-wordpress",
        name="Migracion desde Wix",
        short_description="Te sacamos del hosting lento.",
    ),
]


async def test_suggest_queries_devuelve_combinaciones_nuevas():
    client = MockLLMClient()
    out = await client.suggest_queries(
        country="AR",
        focus="PyMEs",
        existing_queries=["plomero en Cordoba AR"],
        count=5,
    )
    assert len(out) == 5
    # No debe contener la query existente
    assert not any(q.rubro == "plomero" and q.city == "Cordoba" for q in out)
    # Cada sugerencia debe tener rubro, city y reason no vacios
    for q in out:
        assert q.rubro and q.city and q.reason


async def test_classify_reply_not_interested():
    lead = LeadContext(name="X", has_website=False)
    a = await MockLLMClient().classify_reply(
        lead, "No me interesa, gracias.", CATALOG,
    )
    assert a.intent == "not_interested"
    assert a.sentiment == "negative"
    assert "ordenes" in a.suggested_reply.lower() or "exitos" in a.suggested_reply.lower()


async def test_classify_reply_pricing_objection():
    lead = LeadContext(name="X", has_website=False)
    a = await MockLLMClient().classify_reply(
        lead, "Es caro, no tengo presupuesto ahora", CATALOG,
    )
    assert a.intent == "pricing_objection"
    assert "USD" in a.suggested_reply or "presupuesto" in a.suggested_reply.lower()


async def test_classify_reply_ask_meeting():
    lead = LeadContext(name="X", has_website=False)
    a = await MockLLMClient().classify_reply(
        lead, "Podemos coordinar una reunion el jueves?", CATALOG,
    )
    assert a.intent == "ask_meeting"
    assert a.sentiment == "positive"


async def test_classify_reply_wrong_contact():
    lead = LeadContext(name="X", has_website=False)
    a = await MockLLMClient().classify_reply(
        lead, "Hola, te equivocaron de numero", CATALOG,
    )
    assert a.intent == "wrong_contact"


async def test_generate_followup_whatsapp_corto():
    lead = LeadContext(
        name="Panaderia Foo", category="Panaderia", city="Cordoba", has_website=False,
    )
    previous = [
        GeneratedMessage(
            channel="whatsapp", body="Hola Panaderia Foo, te interesa una landing?",
            subject=None, model="mock", prompt_version="v2",
        )
    ]
    follow = await MockLLMClient().generate_followup(
        lead, "whatsapp", CATALOG, previous_messages=previous, days_since_last_contact=5,
    )
    assert follow.channel == "whatsapp"
    assert "Panaderia Foo" in follow.body
    assert len(follow.body) < 500
    assert follow.angle in ("social_proof", "pregunta_directa", "insight_rubro")


async def test_generate_followup_email_tiene_asunto():
    lead = LeadContext(name="Bar Baz", city="Rosario", has_website=False)
    follow = await MockLLMClient().generate_followup(
        lead, "email", CATALOG, previous_messages=None, days_since_last_contact=14,
    )
    assert follow.channel == "email"
    assert follow.subject is not None
    assert "Asunto:" in follow.body
    assert "alphasoftwebs@gmail.com" in follow.body
