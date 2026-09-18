#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.replay import replay_response
from app.schemas import DirectiveInterpretation, OptimizeRequest, OptimizeResponse


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all GridWise public cases over HTTP")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()

    cases = json.loads(
        (ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text()
    )["cases"]
    failures = 0
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
        health = client.get("/health")
        if health.status_code != 200 or health.json() != {"status": "ok"}:
            print("FAIL health endpoint")
            return 1
        for case in cases:
            response = client.post("/optimize-energy", json=case["input"])
            problems: list[str] = []
            if response.status_code != 200:
                problems.append(f"HTTP {response.status_code}")
            else:
                request_model = OptimizeRequest.model_validate(case["input"])
                response_model = OptimizeResponse.model_validate(response.json())
                expected = [
                    DirectiveInterpretation.model_validate(item)
                    for item in case["expected_output"]["directive_interpretation"]
                ]
                if semantics(response_model.directive_interpretation) != semantics(expected):
                    problems.append("interpretation mismatch")
                problems.extend(
                    replay_response(
                        request_model,
                        expected,
                        response_model,
                    )
                )
                expected_cost = case["expected_output"]["total_cost_bdt"]
                if abs(response_model.total_cost_bdt - expected_cost) > 0.01:
                    problems.append(
                        f"cost {response_model.total_cost_bdt} != optimum {expected_cost}"
                    )
            if problems:
                failures += 1
                print(f"FAIL {case['id']}: {'; '.join(problems)}")
            else:
                print(f"PASS {case['id']}")
    print(f"\n{len(cases) - failures}/{len(cases)} public cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
