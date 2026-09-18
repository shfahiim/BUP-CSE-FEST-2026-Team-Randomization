from __future__ import annotations

import copy
import json
from pathlib import Path

from app.optimizer import CHARGE, DISCHARGE, _solve_raw, solve
from app.replay import replay_response
from app.schemas import DirectiveInterpretation, OptimizeRequest, OptimizeResponse

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads(
    (ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text()
)["cases"]


def _response(request, directives, result):
    return OptimizeResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=directives,
        hourly_plan=result.plan,
        total_grid_kwh=result.total_grid_kwh,
        total_cost_bdt=result.total_cost_bdt,
        peak_grid_kwh=result.peak_grid_kwh,
        plan_summary="test",
    )


def test_netting_makes_degenerate_lp_output_schema_representable() -> None:
    cases_requiring_netting = 0
    for raw_case in CASES:
        case = copy.deepcopy(raw_case)
        for hour in case["input"]["hours"]:
            hour["tariff_bdt_per_kwh"] = 0
        request = OptimizeRequest.model_validate(case["input"])
        directives = [
            DirectiveInterpretation.model_validate(item)
            for item in case["expected_output"]["directive_interpretation"]
        ]
        raw = _solve_raw(request, directives)
        simultaneous = [
            hour
            for hour in range(24)
            if raw.x[CHARGE + hour] > 1e-8 and raw.x[DISCHARGE + hour] > 1e-8
        ]
        cases_requiring_netting += bool(simultaneous)
        result = solve(request, directives)
        response = _response(request, directives, result)
        assert replay_response(request, directives, response) == []
    assert cases_requiring_netting >= 3
