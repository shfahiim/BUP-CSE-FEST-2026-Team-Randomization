#!/usr/bin/env python3
"""Exercise the real configured LLM against every public case without printing secrets."""
from __future__ import annotations

import argparse
import asyncio
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

def semantics(items: list[DirectiveInterpretation]) -> list[dict]:
    return [
        {
            "note_index": item.note_index,
            "applies": item.applies,
            "directive_type": item.directive_type,
            "structured_adjustment": (
                None
                if item.structured_adjustment is None
                else item.structured_adjustment.model_dump(mode="json", exclude_none=True)
            ),
        }
        for item in items
    ]


async def run(repetitions: int) -> int:
    if not os.getenv("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 2

    cases = json.loads(
        (ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text()
    )["cases"]
    settings = Settings.from_env()
    # Every repetition must really call the model; cache hits would hide instability.
    settings = Settings(
        gemini_api_key=settings.gemini_api_key,
        gemini_backup_api_key=settings.gemini_backup_api_key,
        gemini_models=settings.gemini_models,
        gemini_base_url=settings.gemini_base_url,
        llm_attempt_timeout_seconds=settings.llm_attempt_timeout_seconds,
        llm_total_timeout_seconds=settings.llm_total_timeout_seconds,
        interpretation_cache_size=0,
        output_decimal_places=settings.output_decimal_places,
    )
    interpreter = GeminiInterpreter(settings)
    service = OptimizationService(interpreter, settings)
    failures = 0
    latencies: list[float] = []
    try:
        for repetition in range(1, repetitions + 1):
            for case in cases:
                request = OptimizeRequest.model_validate(case["input"])
                expected = [
                    DirectiveInterpretation.model_validate(item)
                    for item in case["expected_output"]["directive_interpretation"]
                ]
                started = time.perf_counter()
                response = await service.optimize(request)
                elapsed = time.perf_counter() - started
                latencies.append(elapsed)

                interpretation_ok = semantics(response.directive_interpretation) == semantics(expected)
                self_replay = replay_response(request, response.directive_interpretation, response)
                ground_truth_replay = replay_response(request, expected, response)
                cost_ok = abs(
                    response.total_cost_bdt - case["expected_output"]["total_cost_bdt"]
                ) <= 0.01
                ok = interpretation_ok and not self_replay and not ground_truth_replay and cost_ok
                failures += not ok
                actual_types = ",".join(item.directive_type for item in response.directive_interpretation)
                details: list[str] = []
                if not interpretation_ok:
                    details.append("interpretation")
                if self_replay:
                    details.append("self-replay")
                if ground_truth_replay:
                    details.append("ground-truth-replay")
                if not cost_ok:
                    details.append("cost")
                suffix = "" if not details else f" failures={','.join(details)}"
                print(
                    f"{'PASS' if ok else 'FAIL'} r{repetition} {case['id']} "
                    f"{elapsed:.3f}s types=[{actual_types}]{suffix}"
                )
    finally:
        await interpreter.close()

    ordered = sorted(latencies)
    p95_index = max(0, min(len(ordered) - 1, int(0.95 * len(ordered) + 0.999999) - 1))
    print(
        f"\nsummary: passed={len(latencies) - failures}/{len(latencies)} "
        f"mean={statistics.fmean(latencies):.3f}s p95={ordered[p95_index]:.3f}s "
        f"max={max(latencies):.3f}s"
    )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    return asyncio.run(run(args.repetitions))


if __name__ == "__main__":
    sys.exit(main())
