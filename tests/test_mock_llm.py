"""Tests del MockLLMClient — no requieren DB ni red."""
from __future__ import annotations

import pytest

from app.llm.base import CatalogService, LeadContext
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


async def test_whatsapp_for_no_website_lead():
    lead = LeadContext(name="Panaderia Test", city="Cordoba", has_website=False)
    client = MockLLMClient()
    msg = await client.generate_message(lead, "whatsapp", CATALOG)
    assert msg.channel == "whatsapp"
    assert msg.subject is None
    assert "Panaderia Test" in msg.body
    assert "Cordoba" in msg.body
    assert "no tienen sitio" in msg.body.lower()


async def test_email_for_wix_lead():
    lead = LeadContext(
        name="Atlas Kine",
        city="Rosario",
        has_website=True,
        website="https://atlaskine.wix.com",
        website_status="wix-viejo",
    )
    client = MockLLMClient()
    msg = await client.generate_message(lead, "email", CATALOG)
    assert msg.channel == "email"
    assert msg.subject is not None and "Atlas Kine" in msg.subject
    assert "Wix" in msg.body or "wix" in msg.body.lower() or "carga lento" in msg.body
    assert "alphasoftwebs@gmail.com" in msg.body


async def test_empty_catalog_does_not_crash():
    lead = LeadContext(name="X", city="Y", has_website=False)
    client = MockLLMClient()
    msg = await client.generate_message(lead, "whatsapp", [])
    assert msg.body  # genera algo aunque sea generico


async def test_analyze_lead_no_website_alta_prioridad():
    lead = LeadContext(name="Pizzeria Roma", city="Cordoba", has_website=False)
    intel = await MockLLMClient().analyze_lead(lead, CATALOG, website_status="sin-web")
    assert intel.priority_score >= 7  # sin web = oportunidad clara
    assert intel.pain_points
    assert intel.recommended_service == "landing-page-pyme"


async def test_analyze_lead_wix_recommends_migration():
    lead = LeadContext(
        name="Estudio Lopez", has_website=True, website="https://lopez.wix.com",
    )
    intel = await MockLLMClient().analyze_lead(lead, CATALOG, website_status="wix")
    assert intel.recommended_service == "migracion-wix-wordpress"
    assert "Wix" in intel.priority_reason or "Wix" in intel.pain_points[0]


async def test_analyze_lead_rating_alto_sube_score():
    lead = LeadContext(name="X", has_website=False, rating=4.8)
    intel = await MockLLMClient().analyze_lead(lead, CATALOG, website_status="sin-web")
    # Sin web da 8, +1 por rating alto = 9
    assert intel.priority_score >= 8
