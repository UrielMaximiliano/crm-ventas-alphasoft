"""Selector de LeadProvider segun settings."""
from __future__ import annotations

import logging

from app.config import get_settings
from app.scrapers.base import LeadProvider
from app.scrapers.mock import MockLeadProvider


logger = logging.getLogger(__name__)


_provider: LeadProvider | None = None


def get_lead_provider() -> LeadProvider:
    global _provider
    if _provider is not None:
        return _provider

    s = get_settings()
    if s.mock_scraper:
        logger.info("Scraper mode: MOCK")
        _provider = MockLeadProvider()
    else:
        # Fase 8: aqui se importa GoogleMapsLeadProvider con Scrapling
        try:
            from app.scrapers.google_maps import GoogleMapsLeadProvider  # type: ignore
            logger.info("Scraper mode: GOOGLE_MAPS (Scrapling)")
            _provider = GoogleMapsLeadProvider()
        except ImportError:
            logger.warning(
                "google_maps.py no disponible todavia (Fase 8). Cayendo a MOCK."
            )
            _provider = MockLeadProvider()
    return _provider


def reset_lead_provider() -> None:
    global _provider
    _provider = None
