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
    FollowUpMessage,
    GeneratedMessage,
    LeadContext,
    LeadIntel,
    ReplyAnalysis,
    SuggestedQuery,
)
from app.llm.prompts import (
    ANALYZE_SYSTEM_PROMPT,
    CLASSIFY_REPLY_SYSTEM_PROMPT,
    FOLLOWUP_SYSTEM_PROMPT,
    PROMPT_VERSION,
    SUGGEST_QUERIES_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_analyze_lead_prompt,
    build_classify_reply_prompt,
    build_followup_prompt,
    build_prompt,
    build_suggest_queries_prompt,
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
    async def _call(self, user_prompt: str, *, system_prompt: str | None = None) -> str:
        completion = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
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

    async def suggest_queries(
        self,
        *,
        country: str = "AR",
        focus: str = "PyMEs con baja madurez digital",
        existing_queries: list[str] | None = None,
        count: int = 10,
    ) -> list[SuggestedQuery]:
        prompt = build_suggest_queries_prompt(
            country=country,
            focus=focus,
            existing_queries=existing_queries,
            count=count,
        )
        data = await self._call_json(SUGGEST_QUERIES_SYSTEM_PROMPT, prompt)
        raw_queries = data.get("queries") or data.get("items") or []
        out: list[SuggestedQuery] = []
        for q in raw_queries:
            if not isinstance(q, dict):
                continue
            rubro = str(q.get("rubro", "")).strip().lower()[:60]
            city = str(q.get("city", "")).strip()[:60]
            if not rubro or not city:
                continue
            out.append(
                SuggestedQuery(
                    rubro=rubro,
                    city=city,
                    province=(str(q.get("province", "")).strip() or None),
                    reason=str(q.get("reason", ""))[:200],
                )
            )
        logger.info("Groq suggest_queries devolvio %d queries", len(out))
        return out[:count]

    async def classify_reply(
        self,
        lead: LeadContext,
        raw_reply: str,
        catalog: list[CatalogService],
        *,
        previous_messages: list[GeneratedMessage] | None = None,
    ) -> ReplyAnalysis:
        prompt = build_classify_reply_prompt(
            lead, raw_reply, catalog, previous_messages=previous_messages
        )
        data = await self._call_json(CLASSIFY_REPLY_SYSTEM_PROMPT, prompt)

        valid_intents = {
            "interested", "pricing_objection", "not_interested",
            "ask_info", "ask_meeting", "wrong_contact", "spam", "unknown",
        }
        intent = str(data.get("intent", "unknown")).lower().strip()
        if intent not in valid_intents:
            intent = "unknown"
        sentiment = str(data.get("sentiment", "neutral")).lower().strip()
        if sentiment not in ("positive", "neutral", "negative"):
            sentiment = "neutral"

        logger.info(
            "Groq classify_reply lead=%s intent=%s sentiment=%s",
            lead.name, intent, sentiment,
        )

        return ReplyAnalysis(
            intent=intent,
            sentiment=sentiment,
            summary=str(data.get("summary", ""))[:512],
            suggested_action=str(data.get("suggested_action", ""))[:2000],
            suggested_reply=str(data.get("suggested_reply", ""))[:2000],
            model=self._model,
            prompt_version=PROMPT_VERSION,
        )

    async def generate_followup(
        self,
        lead: LeadContext,
        channel: Channel,
        catalog: list[CatalogService],
        *,
        previous_messages: list[GeneratedMessage] | None = None,
        days_since_last_contact: int = 5,
    ) -> FollowUpMessage:
        prompt = build_followup_prompt(
            lead, channel, catalog,
            previous_messages=previous_messages,
            days_since_last_contact=days_since_last_contact,
        )
        raw = await self._call(prompt, system_prompt=FOLLOWUP_SYSTEM_PROMPT)
        raw = raw.strip()

        if channel == "email":
            subject, body = parse_email_response(raw)
        else:
            subject, body = None, raw

        logger.info(
            "Groq follow-up generated for %s on %s (chars=%d)",
            lead.name, channel, len(body),
        )

        return FollowUpMessage(
            body=body,
            channel=channel,
            subject=subject,
            angle="llm",
            model=self._model,
            prompt_version=f"followup-{PROMPT_VERSION}",
        )
