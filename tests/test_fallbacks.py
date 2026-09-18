from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

from app.guardrails import validate_model_output
from app.replay import replay_response
from app.schemas import OptimizeRequest
from app.service import OptimizationService
from tests.helpers import FailingInterpreter, MappingInterpreter
from tests.test_public_cases import settings

ROOT = Path(__file__).resolve().parents[1]
CASE = json.loads(
    (ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text()
)["cases"][4]


def test_complete_provider_outage_returns_valid_base_schedule() -> None:
    request = OptimizeRequest.model_validate(CASE["input"])
    service = OptimizationService(FailingInterpreter(), settings())
    response = asyncio.run(service.optimize(request))
    assert all(item.directive_type == "no_op" for item in response.directive_interpretation)
    assert replay_response(request, response.directive_interpretation, response) == []


def test_infeasible_interpretation_is_retried_then_discarded_consistently() -> None:
    request_data = copy.deepcopy(CASE["input"])
    request_data["scenario_id"] = "INFEASIBLE"
    request = OptimizeRequest.model_validate(request_data)
    raw = {
        "interpretations": [
            {
                "note_index": 0,
                "directive_type": "max_grid_window",
                "hours": [18, 19, 20],
                "max_grid_kwh": 20,
                "explanation": "Synthetic infeasible cap.",
            }
        ]
    }
    directives = validate_model_output(raw, request)
    mapping = {"INFEASIBLE": [item.model_dump(mode="json") for item in directives]}
    service = OptimizationService(MappingInterpreter(mapping), settings())
    response = asyncio.run(service.optimize(request))
    assert response.directive_interpretation[0].directive_type == "no_op"
    assert response.directive_interpretation[0].structured_adjustment is None
    assert replay_response(request, response.directive_interpretation, response) == []
