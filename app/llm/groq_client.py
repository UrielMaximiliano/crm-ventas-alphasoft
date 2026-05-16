"""Cliente real de Groq (llama-3.3-70b-versatile por default).

Se activa con MOCK_LLM=0 y un GROQ_API_KEY valido.
Get free key: https://console.groq.com/keys
"""
from __future__ import annotations

import json
import logging

from groq import AsyncGroq
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.llm.base import (
    CatalogService,
    Channel,
    GeneratedMessage,
    LeadContext,
    LeadIntel,
)
from app.llm.prompts import (
    ANALYZE_SYSTEM_PROMPT,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_analyze_lead_prompt,
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

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=6),
        reraise=True,
    )
    async def _call_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Llama al LLM forzando response_format=json_object."""
        completion = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,  # baja temp para outputs deterministicos
            max_tokens=900,
            top_p=0.9,
            response_format={"type": "json_object"},
        )
        raw = completion.choices[0].message.content or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Groq devolvio JSON invalido, intentando reparar: %s", raw[:200])
            # Fallback: buscar el primer { y el ultimo }
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(raw[start : end + 1])
            raise

    async def analyze_lead(
        self,
        lead: LeadContext,
        catalog: list[CatalogService],
        *,
        site_html_excerpt: str = "",
        website_status: str = "",
    ) -> LeadIntel:
        prompt = build_analyze_lead_prompt(
            lead,
            catalog,
            site_html_excerpt=site_html_excerpt,
            website_status=website_status,
        )
        data = await self._call_json(ANALYZE_SYSTEM_PROMPT, prompt)

        def _str_list(v) -> list[str]:
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()]
            if isinstance(v, str) and v.strip():
                return [v.strip()]
            return []

        score_raw = data.get("priority_score", 5)
        try:
            score = max(1, min(10, int(score_raw)))
        except (ValueError, TypeError):
            score = 5

        logger.info(
            "Groq analyze_lead for %s: score=%d service=%s pains=%d",
            lead.name, score, data.get("recommended_service", "?"),
            len(data.get("pain_points", [])),
        )

        return LeadIntel(
            priority_score=score,
            priority_reason=str(data.get("priority_reason", ""))[:255],
            site_analysis=str(data.get("site_analysis", ""))[:2000],
            pain_points=_str_list(data.get("pain_points"))[:5],
            recommended_service=str(data.get("recommended_service", ""))[:120],
            extracted_emails=_str_list(data.get("extracted_emails"))[:5],
            extracted_phones=_str_list(data.get("extracted_phones"))[:5],
            model=self._model,
            prompt_version=PROMPT_VERSION,
        )
