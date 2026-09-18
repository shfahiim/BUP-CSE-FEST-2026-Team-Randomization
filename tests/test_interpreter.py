from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.config import Settings
from app.interpreter import GeminiInterpreter
from app.schemas import OptimizeRequest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = json.loads(
    (ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text()
)["cases"][0]


def test_gemini_request_uses_header_json_schema_and_lowercase_thinking_level() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["key"] = request.headers.get("x-goog-api-key")
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        provider_output = {
            "interpretations": [
                {
                    "note_index": 0,
                    "directive_type": "solar_reduction",
                    "hours": [12, 13],
                    "factor": 0.25,
                    "minimum_energy_kwh": None,
                    "max_grid_kwh": None,
                    "explanation": "Cleaning reduces usable solar.",
                },
                {
                    "note_index": 1,
                    "directive_type": "no_op",
                    "hours": None,
                    "factor": None,
                    "minimum_energy_kwh": None,
                    "max_grid_kwh": None,
                    "explanation": "The registration note is out of scope.",
                },
            ]
        }
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(provider_output)}]}}
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        gemini_api_key="secret-test-key",
        gemini_backup_api_key=None,
        gemini_models=("gemini-test",),
        gemini_base_url="https://gemini.invalid/v1beta",
        llm_attempt_timeout_seconds=2,
        llm_total_timeout_seconds=3,
        interpretation_cache_size=0,
        output_decimal_places=6,
    )
    interpreter = GeminiInterpreter(settings, client=client)
    request = OptimizeRequest.model_validate(SAMPLE["input"])
    result = asyncio.run(interpreter.interpret(request))
    asyncio.run(client.aclose())

    assert [item.directive_type for item in result] == ["solar_reduction", "no_op"]
    assert captured["key"] == "secret-test-key"
    assert "secret-test-key" not in captured["url"]
    config = captured["body"]["generationConfig"]
    assert "responseJsonSchema" in config
    assert config["thinkingConfig"]["thinkingLevel"] == "minimal"
