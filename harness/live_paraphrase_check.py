#!/usr/bin/env python3
"""Live hidden-style paraphrase checks for the configured language model."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.interpreter import GeminiInterpreter
from app.replay import replay_response
from app.schemas import DirectiveInterpretation, OptimizeRequest
from app.service import OptimizationService

ALL_HOURS = list(range(24))


def directive(index: int, kind: str, adjustment: dict | None) -> dict:
    return {
        "note_index": index,
        "applies": kind != "no_op",
        "directive_type": kind,
        "structured_adjustment": adjustment,
        "explanation": "Expected synthetic interpretation.",
    }


def semantics(items: list[DirectiveInterpretation]) -> list[tuple]:
    return [
        (
            item.note_index,
            item.applies,
            item.directive_type,
            None
            if item.structured_adjustment is None
            else item.structured_adjustment.model_dump(mode="json", exclude_none=True),
        )
        for item in items
    ]


def build_cases() -> list[tuple[str, dict, list[dict]]]:
    public = json.loads(
        (ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text()
    )["cases"]
    bases = {case["id"]: case["input"] for case in public}
    rows: list[tuple[str, str, str, dict | None]] = [
        ("solar_fraction", "SAMPLE-01", "Today, PV output is capped at one fifth of forecast from 13:00 up to 15:00.", {"hours": [13, 14], "factor": 0.2}),
        ("solar_remaining", "SAMPLE-01", "An inverter test between noon and 3 PM leaves only 75% of the forecast solar usable.", {"hours": [12, 13, 14], "factor": 0.75}),
        ("solar_reduced_by", "SAMPLE-01", "From 10 PM until midnight, usable rooftop generation will be cut by 30 percent.", {"hours": [22, 23], "factor": 0.7}),
        ("solar_midnight", "SAMPLE-01", "During 00:00-02:00 today, only half of normal panel output can be used.", {"hours": [0, 1], "factor": 0.5}),
        ("reserve_percent", "SAMPLE-03", "Maintain a storage floor equal to 45 percent of capacity from 5 PM until 9 PM.", {"hours": [17, 18, 19, 20], "minimum_energy_kwh": 90}),
        ("reserve_late", "SAMPLE-03", "Do not let stored energy fall under 75 kWh between 10 PM and midnight.", {"hours": [22, 23], "minimum_energy_kwh": 75}),
        ("reserve_all_day", "SAMPLE-03", "Throughout the day, retain at least 55 kWh in the battery.", {"hours": ALL_HOURS, "minimum_energy_kwh": 55}),
        ("reserve_24h", "SAMPLE-03", "For 18:00-21:00, 100 kWh must remain available in storage.", {"hours": [18, 19, 20], "minimum_energy_kwh": 100}),
        ("charge_early", "SAMPLE-02", "The charger is locked out from 00:00 until 03:00.", {"hours": [0, 1, 2]}),
        ("charge_wrap", "SAMPLE-02", "Battery charging is unavailable from 10 PM until 2 AM.", {"hours": [0, 1, 22, 23]}),
        ("charge_all_day", "SAMPLE-02", "Do not add energy to the battery at any time today.", {"hours": ALL_HOURS}),
        ("charge_only_discharge", "SAMPLE-02", "The battery may discharge but must not charge between 14:00 and 16:00.", {"hours": [14, 15]}),
        ("discharge_early", "SAMPLE-04", "Disable battery discharge from 01:00 through 04:00.", {"hours": [1, 2, 3]}),
        ("discharge_wrap", "SAMPLE-04", "No energy may leave the battery from 11 PM until 2 AM.", {"hours": [0, 1, 23]}),
        ("discharge_all_day", "SAMPLE-04", "Keep the discharge path disabled throughout today.", {"hours": ALL_HOURS}),
        ("discharge_plain", "SAMPLE-04", "Storage cannot supply the campus during the 17:00-19:00 relay test.", {"hours": [17, 18]}),
        ("grid_ceiling", "SAMPLE-05", "Grid draw has a hard ceiling of 155 kWh per hour from 18:00 until 21:00.", {"hours": [18, 19, 20], "max_grid_kwh": 155}),
        ("grid_at_most", "SAMPLE-05", "Use at most 175 kWh from the utility in each hour between 7 PM and 9 PM.", {"hours": [19, 20], "max_grid_kwh": 175}),
        ("grid_not_above", "SAMPLE-05", "The meter must never read above 190 kWh per hour from 17:00 to 20:00.", {"hours": [17, 18, 19], "max_grid_kwh": 190}),
    ]
    type_for_prefix = {
        "solar": "solar_reduction",
        "reserve": "minimum_battery_reserve",
        "charge": "no_charge_window",
        "discharge": "no_discharge_window",
        "grid": "max_grid_window",
    }
    cases: list[tuple[str, dict, list[dict]]] = []
    for name, base_id, note, adjustment in rows:
        scenario = copy.deepcopy(bases[base_id])
        scenario["scenario_id"] = f"PARA-{name}"
        scenario["operator_notes"] = [note]
        kind = type_for_prefix[name.split("_")[0]]
        cases.append((name, scenario, [directive(0, kind, adjustment)]))

    no_ops = [
        "The battery inspection was completed last week.",
        "Solar panels are scheduled for servicing next month.",
        "A possible feeder cap will be discussed next semester.",
        "Operators should generally review electricity tariffs before annual budgeting.",
        "Yesterday's evening peak exceeded the target.",
    ]
    for index, note in enumerate(no_ops):
        scenario = copy.deepcopy(bases["SAMPLE-01"])
        scenario["scenario_id"] = f"PARA-noop-{index}"
        scenario["operator_notes"] = [note]
        cases.append((f"noop_{index}", scenario, [directive(0, "no_op", None)]))

    combined = copy.deepcopy(bases["SAMPLE-06"])
    combined["scenario_id"] = "PARA-combined"
    combined["operator_notes"] = [
        "Panel testing leaves two thirds of forecast PV usable from 10 AM to noon.",
        "Prevent all battery charging in the 14:00-16:00 interval.",
        "The energy club will meet next month.",
    ]
    cases.append(
        (
            "combined_three_notes",
            combined,
            [
                directive(0, "solar_reduction", {"hours": [10, 11], "factor": 2 / 3}),
                directive(1, "no_charge_window", {"hours": [14, 15]}),
                directive(2, "no_op", None),
            ],
        )
    )
    return cases


async def run() -> int:
    if not os.getenv("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 2
    base = Settings.from_env()
    settings = Settings(
        gemini_api_key=base.gemini_api_key,
        gemini_backup_api_key=base.gemini_backup_api_key,
        gemini_models=base.gemini_models,
        gemini_base_url=base.gemini_base_url,
        llm_attempt_timeout_seconds=base.llm_attempt_timeout_seconds,
        llm_total_timeout_seconds=base.llm_total_timeout_seconds,
        interpretation_cache_size=0,
        output_decimal_places=base.output_decimal_places,
    )
    interpreter = GeminiInterpreter(settings)
    service = OptimizationService(interpreter, settings)
    failures = 0
    latencies: list[float] = []
    try:
        for name, raw_request, raw_expected in build_cases():
            request = OptimizeRequest.model_validate(raw_request)
            expected = [DirectiveInterpretation.model_validate(item) for item in raw_expected]
            started = time.perf_counter()
            response = await service.optimize(request)
            elapsed = time.perf_counter() - started
            latencies.append(elapsed)
            interpretation_ok = semantics(response.directive_interpretation) == semantics(expected)
            ground_truth_errors = replay_response(request, expected, response)
            ok = interpretation_ok and not ground_truth_errors
            failures += not ok
            actual = ",".join(item.directive_type for item in response.directive_interpretation)
            reason = ""
            if not interpretation_ok:
                reason += " interpretation"
            if ground_truth_errors:
                reason += " ground-truth-replay"
            print(f"{'PASS' if ok else 'FAIL'} {name} {elapsed:.3f}s [{actual}]{reason}")
    finally:
        await interpreter.close()
    ordered = sorted(latencies)
    p95 = ordered[max(0, int(0.95 * len(ordered) + 0.999999) - 1)]
    print(
        f"\nsummary: passed={len(latencies)-failures}/{len(latencies)} "
        f"mean={statistics.fmean(latencies):.3f}s p95={p95:.3f}s max={max(latencies):.3f}s"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
