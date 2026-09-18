from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.service import OptimizationService
from tests.helpers import FailingInterpreter
from tests.test_public_cases import settings

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = json.loads(
    (ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text()
)["cases"][0]["input"]


def test_health_and_valid_request() -> None:
    service = OptimizationService(FailingInterpreter(), settings())
    with TestClient(create_app(service)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post("/optimize-energy", json=SAMPLE)
    assert response.status_code == 200
    assert response.json()["scenario_id"] == SAMPLE["scenario_id"]


def test_structurally_invalid_request_returns_400_not_422() -> None:
    service = OptimizationService(FailingInterpreter(), settings())
    invalid = dict(SAMPLE)
    invalid["hours"] = SAMPLE["hours"][:-1]
    with TestClient(create_app(service)) as client:
        response = client.post("/optimize-energy", json=invalid)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_malformed_json_returns_400() -> None:
    service = OptimizationService(FailingInterpreter(), settings())
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/optimize-energy",
            content=b'{"scenario_id":',
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 400


def test_unknown_request_fields_are_ignored() -> None:
    service = OptimizationService(FailingInterpreter(), settings())
    request = dict(SAMPLE)
    request["judge_metadata"] = {"opaque": True}
    with TestClient(create_app(service)) as client:
        response = client.post("/optimize-energy", json=request)
    assert response.status_code == 200


def test_numeric_strings_are_rejected_as_structurally_invalid() -> None:
    service = OptimizationService(FailingInterpreter(), settings())
    request = json.loads(json.dumps(SAMPLE))
    request["battery"]["capacity_kwh"] = "220"
    with TestClient(create_app(service)) as client:
        response = client.post("/optimize-energy", json=request)
    assert response.status_code == 400
