#!/usr/bin/env python3
"""Concurrent end-to-end check against a running GridWise endpoint."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.replay import replay_response
from app.schemas import DirectiveInterpretation, OptimizeRequest, OptimizeResponse


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


async def main_async(base_url: str, concurrency: int) -> int:
    cases = json.loads(
        (ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text()
    )["cases"]
    semaphore = asyncio.Semaphore(concurrency)
    failures: list[str] = []
    latencies: list[float] = []

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30) as client:
        async def run_case(case: dict) -> None:
            async with semaphore:
                started = time.perf_counter()
                response = await client.post("/optimize-energy", json=case["input"])
                elapsed = time.perf_counter() - started
                latencies.append(elapsed)
                if response.status_code != 200:
                    failures.append(f"{case['id']}: HTTP {response.status_code}")
                    return
                request_model = OptimizeRequest.model_validate(case["input"])
                response_model = OptimizeResponse.model_validate(response.json())
                expected = [
                    DirectiveInterpretation.model_validate(item)
                    for item in case["expected_output"]["directive_interpretation"]
                ]
                if semantics(response_model.directive_interpretation) != semantics(expected):
                    failures.append(f"{case['id']}: interpretation")
                if replay_response(request_model, expected, response_model):
                    failures.append(f"{case['id']}: ground-truth replay")
                if abs(response_model.total_cost_bdt - case["expected_output"]["total_cost_bdt"]) > 0.01:
                    failures.append(f"{case['id']}: cost")

        await asyncio.gather(*(run_case(case) for case in cases))

    ordered = sorted(latencies)
    p95 = ordered[max(0, int(0.95 * len(ordered) + 0.999999) - 1)]
    print(
        f"requests={len(latencies)} concurrency={concurrency} failures={len(failures)} "
        f"mean={statistics.fmean(latencies):.3f}s p95={p95:.3f}s max={max(latencies):.3f}s"
    )
    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    return asyncio.run(main_async(args.base_url, args.concurrency))


if __name__ == "__main__":
    sys.exit(main())
