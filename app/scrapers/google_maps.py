"""Scrapper de Google Maps via Scrapling + Camoufox (StealthyFetcher).

Estrategia:
1. Navegar a https://www.google.com/maps/search/{query}
2. Esperar que carguen los resultados en el panel lateral (selector role="article").
3. Hacer scroll del panel lateral con delays randomizados para que cargue mas paginas.
4. Extraer cada resultado: nombre, rating, telefono, sitio, direccion.

ADVERTENCIA: el HTML de Google Maps cambia frecuentemente. Si los selectores
dejan de matchear, ajustar `parse_results()`. El codigo esta escrito defensivo
para que cuando un campo falte, devuelva None en vez de explotar.

Riesgo: Google puede mostrar captcha o bloquear la IP si se hace mucho volumen.
Mantener `scraper_daily_limit` bajo en .env (default 100/dia).
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from app.config import get_settings
from app.scrapers.base import LeadProvider, ScrapedLead, SearchQuery


logger = logging.getLogger(__name__)


_RATING_RX = re.compile(r"(\d[.,]\d)")
_REVIEWS_RX = re.compile(r"\(\s*([\d.\,]+)\s*\)")
_REVIEWS_ARIA_RX = re.compile(r"(\d[\d.,]*)\s*rese[nñ]as?", re.IGNORECASE)
_PHONE_RX = re.compile(r"[\+\(]?\d[\d\s\-\(\)]{6,}")

# Direccion argentina: arranca con palabra tipica de calle o tiene numero claro.
# Si el string es solo categoria/descripcion del negocio, NO matchea.
_ADDRESS_STARTS = (
    "av.", "avda.", "av ", "avda ",
    "bv.", "blvd.", "bv ", "bvd.", "bvar.",
    "calle ", "ruta ", "diagonal ", "pasaje ", "paseo ", "camino ",
)
_ADDRESS_NUM_RX = re.compile(r"\b\d{2,5}\b")
_POSTAL_RX = re.compile(r"\b[A-Z]\d{4}[A-Z]{0,3}\b")
_BIZ_DESCRIPTION_PREFIXES = (
    "bar", "comedor", "restaurante", "restaurant", "parrilla", "rosticeria",
    "panaderia", "cafeteria", "cafe", "pizzeria", "heladeria", "comida",
    "cocina", "gastronom", "bebidas",
)


def _looks_like_address(s: str | None) -> bool:
    if not s or len(s) < 6:
        return False
    sl = s.lower().strip()
    # Empieza con palabra tipica de direccion
    if any(sl.startswith(p) for p in _ADDRESS_STARTS):
        return True
    # Codigo postal argentino o numero claro de altura
    if _POSTAL_RX.search(s) or _ADDRESS_NUM_RX.search(s):
        # Pero descartar si empieza con descripcion del negocio
        if any(sl.startswith(p) for p in _BIZ_DESCRIPTION_PREFIXES):
            return False
        return True
    return False


def _first(node: Any, selector: str) -> Any | None:
    matches = node.css(selector)
    return matches[0] if matches else None


def _node_text(node: Any | None) -> str | None:
    if node is None:
        return None
    text = getattr(node, "text", None)
    if text:
        return str(text).strip()
    try:
        return str(node.get_all_text(" ")).strip()
    except Exception:
        return None


def _clean_google_url(href: str | None) -> str | None:
    if not href:
        return None
    parsed = urlparse(href)
    if "google." in parsed.netloc and parsed.path == "/url":
        target = parse_qs(parsed.query).get("q", [None])[0]
        return unquote(target) if target else href
    return href


def _looks_like_external_website(href: str | None) -> bool:
    if not href or not href.startswith(("http://", "https://")):
        return False
    host = urlparse(href).netloc.lower()
    blocked = (
        "google.",
        "gstatic.",
        "ggpht.",
        "googleusercontent.",
        "accounts.google.",
        "support.google.",
    )
    return not any(part in host for part in blocked)


def _to_float(value: str | None) -> float | None:
    if not value:
        return None
    m = _RATING_RX.search(value)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _to_int_reviews(value: str | None) -> int | None:
    if not value:
        return None
    m = _REVIEWS_RX.search(value)
    if not m:
        return None
    s = m.group(1).replace(".", "").replace(",", "")
    try:
        return int(s)
    except ValueError:
        return None


async def _scroll_results(page: Any, *, rounds: int = 8) -> None:
    """Hace scroll del panel lateral de resultados con delays randomizados."""
    settings = get_settings()
    selector = 'div[role="feed"]'  # Panel scrollable de Maps en 2024-2026
    for _ in range(rounds):
        await page.evaluate(
            "(sel) => { const el = document.querySelector(sel); if (el) el.scrollBy(0, el.clientHeight); }",
            selector,
        )
        delay = random.uniform(settings.scraper_min_delay_sec, settings.scraper_max_delay_sec)
        await asyncio.sleep(delay)


def _maps_url(query_text: str) -> str:
    # /maps/search/ admite query con espacios codificados
    return f"https://www.google.com/maps/search/{quote_plus(query_text)}?hl=es-419&gl=ar"


class GoogleMapsLeadProvider:
    name = "google_maps"

    def __init__(self) -> None:
        # Import perezoso para no requerir scrapling si MOCK_SCRAPER=1
        from scrapling import StealthyFetcher  # type: ignore

        self._fetcher = StealthyFetcher

    async def search(
        self,
        query: SearchQuery,
        *,
        max_results: int = 20,
    ) -> list[ScrapedLead]:
        url = _maps_url(query.text)
        logger.info("Scraping Google Maps: %s", url)

        async def page_action(page):  # noqa: ANN001
            await page.wait_for_selector('div[role="feed"]', timeout=15000)
            rounds = max(3, max_results // 5)
            await _scroll_results(page, rounds=rounds)
            return page

        try:
            page = await self._fetcher.async_fetch(
                url,
                headless=True,
                network_idle=True,
                page_action=page_action,
                timeout=60000,
                disable_resources=False,
                google_search=False,
            )
        except Exception:
            logger.exception("Falla al hacer fetch de Google Maps")
            return []

        leads = self._parse_results(page, query, max_results=max_results)
        for lead in leads:
            await self._fill_detail_fields(lead)
            if lead.source_id and len(lead.source_id) > 250:
                lead.source_id = lead.source_id[:250]
        return leads

    async def _fill_detail_fields(self, lead: ScrapedLead) -> None:
        """Abre el detalle de Maps para obtener telefono/web/direccion si existen."""
        if not lead.source_id:
            return

        async def page_action(page):  # noqa: ANN001
            await page.wait_for_selector('div[role="main"], h1', timeout=15000)
            return page

        try:
            detail = await self._fetcher.async_fetch(
                lead.source_id,
                headless=True,
                network_idle=True,
                page_action=page_action,
                timeout=60000,
                disable_resources=False,
                google_search=False,
            )
        except Exception:
            logger.exception("Falla al abrir detalle de Maps para %s", lead.name)
            return

        # El detalle de Maps es la fuente de verdad: sobrescribe address/phone
        # SIEMPRE que esten ahi (la heuristica del listado puede haber agarrado
        # la descripcion del negocio en vez de la direccion real).
        for button in detail.css("button[aria-label]"):
            label = button.attrib.get("aria-label", "").strip()
            label_lower = label.lower()
            if label_lower.startswith(("dirección:", "direccion:")):
                detail_addr = label.split(":", 1)[1].strip()
                if detail_addr:
                    lead.address = detail_addr
            elif label_lower.startswith(("teléfono:", "telefono:")):
                detail_phone = label.split(":", 1)[1].strip()
                if detail_phone:
                    lead.phone = detail_phone

        # Reviews count: lo extraemos del aria-label del rating si esta presente
        # ("4.8 estrellas 230 reseñas"). En el listado solo viene el numero entre
        # parentesis que a veces no se renderiza. Iteramos en Python porque el
        # selector CSS case-insensitive ([attr*="x" i]) no es soportado por parsel.
        if not lead.reviews_count:
            for el in detail.css("[aria-label]"):
                aria = el.attrib.get("aria-label", "")
                if not aria or ("rese" not in aria.lower() and "estrell" not in aria.lower()):
                    continue
                m = _REVIEWS_ARIA_RX.search(aria)
                if m:
                    try:
                        n = int(m.group(1).replace(".", "").replace(",", ""))
                        lead.reviews_count = n
                        break
                    except ValueError:
                        pass

        for anchor in detail.css("a[href]"):
            href = anchor.attrib.get("href")
            label = anchor.attrib.get("aria-label", "").strip().lower()
            if href and href.startswith("tel:") and not lead.phone:
                lead.phone = href.replace("tel:", "").strip()
            if "sitio web" in label and not lead.website:
                lead.website = _clean_google_url(href)
            if not lead.website:
                cleaned = _clean_google_url(href)
                if _looks_like_external_website(cleaned):
                    lead.website = cleaned

        if lead.phone:
            m = _PHONE_RX.search(lead.phone)
            lead.phone = m.group(0).strip() if m else lead.phone.strip()

    def _parse_results(
        self,
        page: Any,
        query: SearchQuery,
        *,
        max_results: int,
    ) -> list[ScrapedLead]:
        """Extrae leads del HTML resultante.

        Los selectores se basan en la estructura de Google Maps en 2024-2026.
        Tienen fallbacks para minimizar quebraduras en cambios menores.
        """
        leads: list[ScrapedLead] = []
        try:
            cards = page.css('div[role="article"]')
        except Exception:
            logger.exception("No se pudo seleccionar los cards de resultados.")
            return leads

        for card in cards[:max_results]:
            try:
                anchor = _first(card, 'a[aria-label][href*="/maps/place/"]')
                href = anchor.attrib.get("href") if anchor else None
                name = anchor.attrib.get("aria-label") if anchor else None
                if not name:
                    name_el = _first(card, "div.qBF1Pd, div.fontHeadlineSmall")
                    name = _node_text(name_el)
                if not name:
                    continue

                rating_el = _first(card, 'span.MW4etd, span[aria-label*="estrella"]')
                rating = _to_float(_node_text(rating_el))

                reviews_el = _first(card, "span.UY7F9")
                reviews_count = _to_int_reviews(_node_text(reviews_el))

                # La direccion suele estar en spans con clase Io6YTe o W4Efsd
                info_spans = [_node_text(s) for s in card.css("div.W4Efsd span")]
                info_spans = [s for s in info_spans if s and len(s) > 2]

                phone = None
                address = None
                category = None
                website = None

                # Heuristicas: telefono empieza con + o (, direccion solo si
                # parece direccion argentina real (no la descripcion del negocio)
                for s in info_spans:
                    if not phone and _PHONE_RX.search(s):
                        phone = s.strip()
                    elif not address and _looks_like_address(s):
                        address = s.strip()

                for s in info_spans:
                    if s == name or s == "Patrocinado" or s == "Abierto":
                        continue
                    if _to_float(s) is not None or _PHONE_RX.search(s):
                        continue
                    if "cierra" in s.lower() or "abre" in s.lower():
                        continue
                    category = s
                    break

                website_el = _first(card, 'a[data-value="Sitio web"]')
                if website_el is None:
                    website_el = _first(card, 'a[aria-label*="itio"]')
                if website_el:
                    website = _clean_google_url(website_el.attrib.get("href"))

                leads.append(
                    ScrapedLead(
                        name=name,
                        category=category or query.rubro,
                        city=query.city,
                        province=query.province,
                        country=query.country,
                        phone=phone,
                        email=None,  # Maps no expone email
                        website=website,
                        address=address,
                        rating=rating,
                        reviews_count=reviews_count,
                        source="google_maps",
                        source_id=href,
                        search_query=query.text,
                    )
                )
            except Exception:
                logger.exception("Falla parseando un card. Salteo.")
                continue

        logger.info("Google Maps devolvio %d leads para '%s'", len(leads), query.text)
        return leads
