"""Heuristicas para decidir si un sitio web esta "lo suficientemente mal" como
para que valga la pena ofrecer migracion o landing nueva.

Se ejecuta por lead via el job `enrich`. Output: un `WebsiteAssessment` que
incluye un `qualification_reason` legible para humanos.

Reglas (en orden de severidad):
1. URL no resoluble / TLS roto / 5xx -> "sitio caido"
2. 4xx                                -> "sitio con error 4xx"
3. Plataforma vieja (Wix/Joomla detectado)  -> "plataforma desactualizada"
4. Copyright en footer >= 3 anios atras      -> "sitio sin actualizar"
5. Sin viewport meta (no mobile-friendly)    -> "no mobile-friendly"
6. Tarda > 4s en TTFB                        -> "carga lenta"
Si nada aplica -> sitio "OK" (no es lead calificado por esta heuristica).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import httpx


logger = logging.getLogger(__name__)


_GENERATOR_RX = re.compile(
    r'<meta\s+name=["\']generator["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_COPYRIGHT_RX = re.compile(r"(?:©|copyright|&copy;)[^\d]{0,40}(\d{4})", re.IGNORECASE)
_VIEWPORT_RX = re.compile(r'<meta\s+name=["\']viewport["\']', re.IGNORECASE)
_TITLE_RX = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)

# Emails de contacto en el HTML. Buscamos mailto: primero y despues regex generico.
_MAILTO_RX = re.compile(r'mailto:([\w.+-]+@[\w-]+\.[\w.-]+)', re.IGNORECASE)
_EMAIL_RX = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
_EMAIL_BLOCKLIST = (
    "example.com", "example.org", "test.com", "domain.com", "yourdomain",
    "sentry.io", "wixpress.com", "wix.com", "godaddy.com",
    "@2x", "@3x",  # falsos positivos de retina images
)


def extract_emails(body: str) -> list[str]:
    """Saca emails del HTML priorizando los de tag <a href='mailto:'>.

    Filtra ejemplos genericos (example.com), trackers de plataformas y
    duplicados. Devuelve lista ordenada por relevancia (mailto: primero).
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in _MAILTO_RX.findall(body):
        e = raw.strip().lower()
        if e in seen:
            continue
        if any(b in e for b in _EMAIL_BLOCKLIST):
            continue
        seen.add(e)
        out.append(e)
    for raw in _EMAIL_RX.findall(body):
        e = raw.strip().lower()
        if e in seen:
            continue
        if any(b in e for b in _EMAIL_BLOCKLIST):
            continue
        seen.add(e)
        out.append(e)
    return out

# Indicadores de plataformas que solemos ofertar migrar
_OLD_PLATFORMS = ("wix", "joomla", "wordpress 4.", "wordpress 5.0", "wordpress 5.1")
_NOT_OWN_WEBSITE_HOSTS = (
    "wa.me",
    "whatsapp.com",
    "linktr.ee",
    "instagram.com",
    "facebook.com",
    "sites.google.com",
    "bio.link",
    "beacons.ai",
    "taplink.cc",
)


@dataclass(slots=True)
class WebsiteAssessment:
    url: str
    reachable: bool
    http_status: int | None
    ttfb_ms: int | None
    title: str | None
    generator: str | None
    has_viewport: bool
    copyright_year: int | None
    qualifies: bool
    reason: str
    status_tag: str  # "sin-web", "caido", "error-4xx", "wix", "wordpress-viejo", "viejo", "no-mobile", "ok"
    emails: tuple[str, ...] = ()  # emails encontrados en el HTML


def assess_no_website() -> WebsiteAssessment:
    return WebsiteAssessment(
        url="",
        reachable=False,
        http_status=None,
        ttfb_ms=None,
        title=None,
        generator=None,
        has_viewport=False,
        copyright_year=None,
        qualifies=True,
        reason="sin sitio web",
        status_tag="sin-web",
    )


async def assess_website(url: str, *, timeout: float = 8.0) -> WebsiteAssessment:
    if not url:
        return assess_no_website()

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    host = urlparse(url).netloc.lower().removeprefix("www.")
    if any(host == h or host.endswith("." + h) for h in _NOT_OWN_WEBSITE_HOSTS):
        return WebsiteAssessment(
            url=url,
            reachable=True,
            http_status=None,
            ttfb_ms=None,
            title=None,
            generator=None,
            has_viewport=True,
            copyright_year=None,
            qualifies=True,
            reason="sin sitio propio (usa WhatsApp/red social)",
            status_tag="link-social",
        )

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120 Safari/537.36"
                )
            },
        ) as client:
            t0 = datetime.now()
            resp = await client.get(url)
            ttfb_ms = int((datetime.now() - t0).total_seconds() * 1000)
    except (httpx.RequestError, httpx.HTTPError) as exc:
        logger.info("Sitio %s no reachable: %r", url, exc)
        return WebsiteAssessment(
            url=url,
            reachable=False,
            http_status=None,
            ttfb_ms=None,
            title=None,
            generator=None,
            has_viewport=False,
            copyright_year=None,
            qualifies=True,
            reason="sitio caido o sin DNS",
            status_tag="caido",
        )

    status = resp.status_code
    body = resp.text if resp.text else ""

    # Emails se extraen una sola vez para todas las ramas que tengan body util
    emails_t = tuple(extract_emails(body))

    if 500 <= status < 600:
        return WebsiteAssessment(
            url=url, reachable=True, http_status=status, ttfb_ms=ttfb_ms,
            title=None, generator=None, has_viewport=False, copyright_year=None,
            qualifies=True, reason=f"sitio con error {status}", status_tag="caido",
            emails=emails_t,
        )
    if 400 <= status < 500:
        return WebsiteAssessment(
            url=url, reachable=True, http_status=status, ttfb_ms=ttfb_ms,
            title=None, generator=None, has_viewport=False, copyright_year=None,
            qualifies=True, reason=f"sitio con error {status}", status_tag="error-4xx",
            emails=emails_t,
        )

    title_m = _TITLE_RX.search(body)
    title = title_m.group(1).strip() if title_m else None

    gen_m = _GENERATOR_RX.search(body)
    generator = gen_m.group(1).strip() if gen_m else None

    has_viewport = bool(_VIEWPORT_RX.search(body))

    cy_m = _COPYRIGHT_RX.search(body)
    copyright_year = None
    if cy_m:
        try:
            copyright_year = int(cy_m.group(1))
        except ValueError:
            pass

    # Detectar plataforma vieja
    blob_lower = (generator or "").lower() + " " + body[:5000].lower()
    for platform in _OLD_PLATFORMS:
        if platform in blob_lower:
            return WebsiteAssessment(
                url=url, reachable=True, http_status=status, ttfb_ms=ttfb_ms,
                title=title, generator=generator, has_viewport=has_viewport,
                copyright_year=copyright_year,
                qualifies=True,
                reason=f"plataforma desactualizada ({platform.split()[0]})",
                status_tag="wix" if "wix" in platform else "viejo",
                emails=emails_t,
            )

    now_year = datetime.now().year
    if copyright_year and copyright_year < now_year - 3:
        return WebsiteAssessment(
            url=url, reachable=True, http_status=status, ttfb_ms=ttfb_ms,
            title=title, generator=generator, has_viewport=has_viewport,
            copyright_year=copyright_year,
            qualifies=True,
            reason=f"sin actualizar desde {copyright_year}",
            status_tag="viejo",
            emails=emails_t,
        )

    if not has_viewport:
        return WebsiteAssessment(
            url=url, reachable=True, http_status=status, ttfb_ms=ttfb_ms,
            title=title, generator=generator, has_viewport=False,
            copyright_year=copyright_year,
            qualifies=True,
            reason="no es mobile-friendly",
            status_tag="no-mobile",
            emails=emails_t,
        )

    if ttfb_ms and ttfb_ms > 4000:
        return WebsiteAssessment(
            url=url, reachable=True, http_status=status, ttfb_ms=ttfb_ms,
            title=title, generator=generator, has_viewport=has_viewport,
            copyright_year=copyright_year,
            qualifies=True,
            reason=f"carga lenta ({ttfb_ms}ms)",
            status_tag="lento",
            emails=emails_t,
        )

    return WebsiteAssessment(
        url=url, reachable=True, http_status=status, ttfb_ms=ttfb_ms,
        title=title, generator=generator, has_viewport=has_viewport,
        copyright_year=copyright_year,
        qualifies=False,
        reason="sitio en buen estado",
        status_tag="ok",
        emails=emails_t,
    )
