"""Selector de cliente LLM segun settings.

Uso:
    client = get_llm_client()
    msg = await client.generate_message(lead, channel, catalog)
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.llm.base import LLMClient
from app.llm.mock import MockLLMClient


logger = logging.getLogger(__name__)


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Devuelve un singleton de LLMClient.

    Si MOCK_LLM=1 o no hay GROQ_API_KEY -> MockLLMClient.
    Si MOCK_LLM=0 y hay API key -> GroqLLMClient.
    """
    global _client
    if _client is not None:
        return _client

    s = get_settings()
    if s.mock_llm or not s.groq_api_key:
        logger.info(
            "LLM mode: MOCK (mock_llm=%s, has_key=%s)",
            s.mock_llm, bool(s.groq_api_key),
        )
        _client = MockLLMClient()
    else:
        # Import perezoso para no requerir `groq` en tests/CI
        from app.llm.groq_client import GroqLLMClient
        logger.info("LLM mode: GROQ (model=%s)", s.groq_model)
        _client = GroqLLMClient()
    return _client


def reset_llm_client() -> None:
    """Resetea el singleton (util para tests y para cambiar settings en runtime)."""
    global _client
    _client = None
