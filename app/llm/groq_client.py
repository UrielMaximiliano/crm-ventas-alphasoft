"""Cliente real de Groq (llama-3.3-70b-versatile por default).

Se activa con MOCK_LLM=0 y un GROQ_API_KEY valido.
Get free key: https://console.groq.com/keys
"""
from __future__ import annotations

import logging

from groq import AsyncGroq
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.llm.base import CatalogService, Channel, GeneratedMessage, LeadContext
from app.llm.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_prompt,
    parse_email_response,
)


logger = logging.getLogger(__name__)


class GroqLLMClient:
    name = "groq"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        s = get_settings()
        key = api_key or s.groq_api_key
        if not key:
            raise RuntimeError(
                "GROQ_API_KEY vacio. Conseguir gratis en https://console.groq.com/keys"
            )
        self._client = AsyncGroq(api_key=key)
        self._model = model or s.groq_model

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _call(self, user_prompt: str) -> str:
        completion = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=600,
            top_p=0.9,
        )
        return completion.choices[0].message.content or ""

    async def generate_message(
        self,
        lead: LeadContext,
        channel: Channel,
        catalog: list[CatalogService],
    ) -> GeneratedMessage:
        prompt = build_prompt(lead, channel, catalog)
        raw = await self._call(prompt)
        raw = raw.strip()

        if channel == "email":
            subject, body = parse_email_response(raw)
        else:
            subject, body = None, raw

        logger.info(
            "Groq generated %s for %s (model=%s chars=%d)",
            channel, lead.name, self._model, len(body),
        )

        return GeneratedMessage(
            channel=channel,
            body=body,
            subject=subject,
            model=self._model,
            prompt_version=PROMPT_VERSION,
        )
