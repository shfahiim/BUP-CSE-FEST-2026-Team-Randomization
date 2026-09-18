from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import Settings
from app.replay import replay_response
from app.schemas import OptimizeRequest
from app.service import OptimizationService
from tests.helpers import MappingInterpreter

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads(
    (ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text()
)["cases"]


def settings() -> Settings:
    return Settings(
        gemini_api_key=None,
        gemini_backup_api_key=None,
        gemini_models=("fixture",),
        gemini_base_url="https://invalid.example",
        llm_attempt_timeout_seconds=1,
        llm_total_timeout_seconds=1,
        interpretation_cache_size=0,
        output_decimal_places=6,
    )


def test_all_public_cases_match_interpretation_validity_and_optimal_cost() -> None:
    mapping = {
        case["id"]: case["expected_output"]["directive_interpretation"] for case in CASES
    }
    service = OptimizationService(MappingInterpreter(mapping), settings())

    for case in CASES:
        request = OptimizeRequest.model_validate(case["input"])
        response = asyncio.run(service.optimize(request))
        actual = [item.model_dump(mode="json") for item in response.directive_interpretation]
        assert actual == case["expected_output"]["directive_interpretation"]
        assert replay_response(request, response.directive_interpretation, response) == []
        assert abs(response.total_cost_bdt - case["expected_output"]["total_cost_bdt"]) <= 0.01
        assert abs(response.total_grid_kwh - case["expected_output"]["total_grid_kwh"]) <= 0.01
