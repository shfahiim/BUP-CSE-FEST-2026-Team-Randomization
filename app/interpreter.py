from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict

import httpx

from app.config import Settings
from app.errors import DirectiveValidationError, InterpretationError
from app.guardrails import validate_model_output
from app.prompts import SYSTEM_PROMPT, user_prompt
from app.schemas import DirectiveInterpretation, OptimizeRequest

logger = logging.getLogger(__name__)


class GeminiInterpreter:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient()
        self._cache: OrderedDict[str, list[DirectiveInterpretation]] = OrderedDict()
        self._cache_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def interpret(
        self,
        request: OptimizeRequest,
        *,
        feedback: str | None = None,
        bypass_cache: bool = False,
    ) -> list[DirectiveInterpretation]:
        key = self._cache_key(request, feedback)
        if not bypass_cache:
            cached = await self._cache_get(key)
            if cached is not None:
                return cached

        attempts = self._attempts()
        if not attempts:
            raise InterpretationError("no Gemini API key is configured")

        deadline = time.monotonic() + self.settings.llm_total_timeout_seconds
        errors: list[str] = []
        validation_feedback = feedback
        for model, api_key in attempts:
            remaining = deadline - time.monotonic()
            if remaining <= 0.1:
                break
            timeout = min(self.settings.llm_attempt_timeout_seconds, remaining)
            started = time.perf_counter()
            try:
                payload = await self._call_model(
                    request,
                    model=model,
                    api_key=api_key,
                    timeout=timeout,
                    feedback=validation_feedback,
                )
                directives = validate_model_output(payload, request)
                await self._cache_put(key, directives)
                logger.info(
                    "llm attempt succeeded model=%s elapsed_ms=%d",
                    model,
                    round((time.perf_counter() - started) * 1000),
                )
                return [item.model_copy(deep=True) for item in directives]
            except DirectiveValidationError as exc:
                errors.append(f"{model}: validation failed")
                validation_feedback = str(exc)
                logger.warning(
                    "llm attempt rejected model=%s reason=validation elapsed_ms=%d",
                    model,
                    round((time.perf_counter() - started) * 1000),
                )
            except httpx.HTTPStatusError as exc:
                errors.append(f"{model}: provider call failed")
                logger.warning(
                    "llm attempt failed model=%s reason=http_%d elapsed_ms=%d",
                    model,
                    exc.response.status_code,
                    round((time.perf_counter() - started) * 1000),
                )
            except (httpx.HTTPError, asyncio.TimeoutError, ValueError, KeyError, IndexError):
                errors.append(f"{model}: provider call failed")
                logger.warning(
                    "llm attempt failed model=%s reason=transport_or_payload elapsed_ms=%d",
                    model,
                    round((time.perf_counter() - started) * 1000),
                )

        raise InterpretationError("; ".join(errors) or "interpretation deadline exceeded")

    def _attempts(self) -> list[tuple[str, str]]:
        keys = [key for key in (self.settings.gemini_api_key, self.settings.gemini_backup_api_key) if key]
        if not keys:
            return []
        attempts: list[tuple[str, str]] = []
        for index, model in enumerate(self.settings.gemini_models):
            key = keys[min(index, len(keys) - 1)]
            attempts.append((model, key))
        return attempts

    async def _call_model(
        self,
        request: OptimizeRequest,
        *,
        model: str,
        api_key: str,
        timeout: float,
        feedback: str | None,
    ) -> object:
        count = len(request.operator_notes)
        schema = {
            "type": "object",
            "properties": {
                "interpretations": {
                    "type": "array",
                    "minItems": count,
                    "maxItems": count,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "note_index": {"type": "integer", "minimum": 0, "maximum": count - 1},
                            "directive_type": {
                                "type": "string",
                                "enum": [
                                    "solar_reduction",
                                    "minimum_battery_reserve",
                                    "no_charge_window",
                                    "no_discharge_window",
                                    "max_grid_window",
                                    "no_op",
                                ],
                            },
                            "hours": {
                                "type": ["array", "null"],
                                "items": {"type": "integer", "minimum": 0, "maximum": 23},
                                "maxItems": 24,
                            },
                            "factor": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                            "minimum_energy_kwh": {"type": ["number", "null"], "minimum": 0},
                            "max_grid_kwh": {"type": ["number", "null"], "minimum": 0},
                            "explanation": {"type": "string"},
                        },
                        "required": [
                            "note_index",
                            "directive_type",
                            "hours",
                            "factor",
                            "minimum_energy_kwh",
                            "max_grid_kwh",
                            "explanation",
                        ],
                    },
                }
            },
            "required": ["interpretations"],
            "additionalProperties": False,
        }
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": user_prompt(
                                request.operator_notes,
                                request.battery.capacity_kwh,
                                feedback,
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "temperature": 0,
                "seed": 2026,
                "candidateCount": 1,
                "maxOutputTokens": 2048,
                "thinkingConfig": {"thinkingLevel": "minimal"},
            },
        }
        url = f"{self.settings.gemini_base_url}/models/{model}:generateContent"
        response = await self.client.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                "Gemini request failed",
                request=response.request,
                response=response,
            )
        data = response.json()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts if not part.get("thought", False))
        if not text.strip():
            raise ValueError("Gemini returned no structured text")
        return json.loads(text)

    def _cache_key(self, request: OptimizeRequest, feedback: str | None) -> str:
        raw = json.dumps(
            {
                "notes": [note.strip() for note in request.operator_notes],
                "capacity": request.battery.capacity_kwh,
                "models": self.settings.gemini_models,
                "feedback": feedback,
                "prompt": SYSTEM_PROMPT,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    async def _cache_get(self, key: str) -> list[DirectiveInterpretation] | None:
        async with self._cache_lock:
            value = self._cache.get(key)
            if value is None:
                return None
            self._cache.move_to_end(key)
            return [item.model_copy(deep=True) for item in value]

    async def _cache_put(self, key: str, value: list[DirectiveInterpretation]) -> None:
        if self.settings.interpretation_cache_size <= 0:
            return
        async with self._cache_lock:
            self._cache[key] = [item.model_copy(deep=True) for item in value]
            self._cache.move_to_end(key)
            while len(self._cache) > self.settings.interpretation_cache_size:
                self._cache.popitem(last=False)
