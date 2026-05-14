"""Tests de las heuristicas de website_check.

Estos tests NO hacen red real; mockean httpx.AsyncClient.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.enrich.website_check import assess_no_website, assess_website


def test_assess_no_website_califica():
    a = assess_no_website()
    assert a.qualifies is True
    assert a.status_tag == "sin-web"
    assert "sin sitio" in a.reason.lower()


async def test_wix_es_calificado():
    body = """
    <html><head>
      <meta name="generator" content="Wix.com Website Builder"/>
      <meta name="viewport" content="width=device-width"/>
    </head><body>Hola</body></html>
    """
    response = httpx.Response(200, text=body)
    with patch("app.enrich.website_check.httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=response)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = instance

        a = await assess_website("https://test.wix.com")
        assert a.qualifies is True
        assert a.status_tag == "wix"


async def test_sitio_ok_no_califica():
    body = f"""
    <html><head>
      <meta name="viewport" content="width=device-width"/>
      <title>Negocio Moderno</title>
    </head><body>
      <footer>© {datetime.now().year} Negocio Moderno</footer>
    </body></html>
    """
    response = httpx.Response(200, text=body)
    with patch("app.enrich.website_check.httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=response)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = instance

        a = await assess_website("https://moderno.com")
        assert a.qualifies is False
        assert a.status_tag == "ok"


async def test_sitio_caido_califica():
    with patch("app.enrich.website_check.httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(side_effect=httpx.ConnectError("DNS fail"))
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = instance

        a = await assess_website("https://nope.invalid")
        assert a.qualifies is True
        assert a.status_tag == "caido"


async def test_link_social_califica_como_sin_sitio_propio():
    a = await assess_website("https://wa.me/5493511234567")
    assert a.qualifies is True
    assert a.status_tag == "link-social"
    assert "sin sitio propio" in a.reason
