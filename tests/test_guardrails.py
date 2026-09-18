from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.errors import DirectiveValidationError
from app.guardrails import validate_model_output
from app.schemas import OptimizeRequest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = json.loads(
    (ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text()
)["cases"][0]
REQUEST = OptimizeRequest.model_validate(SAMPLE["input"])


def test_canonicalizes_meaning_preserving_hour_representation() -> None:
    raw = {
        "interpretations": [
            {
                "note_index": 0.0,
                "directive_type": "solar_reduction",
                "hours": [13.0, 12, 13],
                "factor": 0.25,
                "explanation": "Panel cleaning reduces usable solar.",
            },
            {"note_index": 1, "directive_type": "no_op", "explanation": "Out of scope."},
        ]
    }
    result = validate_model_output(raw, REQUEST)
    assert result[0].structured_adjustment.hours == [12, 13]
    assert result[1].applies is False


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_rejects_non_finite_reserve(bad_value: float) -> None:
    raw = {
        "interpretations": [
            {
                "note_index": 0,
                "directive_type": "minimum_battery_reserve",
                "hours": [18],
                "minimum_energy_kwh": bad_value,
            },
            {"note_index": 1, "directive_type": "no_op"},
        ]
    }
    with pytest.raises(DirectiveValidationError):
        validate_model_output(raw, REQUEST)


@pytest.mark.parametrize("field", ["factor", "max_grid_kwh"])
def test_rejects_other_non_finite_directive_numbers(field: str) -> None:
    directive_type = "solar_reduction" if field == "factor" else "max_grid_window"
    raw = {
        "interpretations": [
            {
                "note_index": 0,
                "directive_type": directive_type,
                "hours": [12],
                field: float("nan"),
            },
            {"note_index": 1, "directive_type": "no_op"},
        ]
    }
    with pytest.raises(DirectiveValidationError):
        validate_model_output(raw, REQUEST)


def test_rejects_missing_note_mapping() -> None:
    with pytest.raises(DirectiveValidationError):
        validate_model_output(
            {"interpretations": [{"note_index": 0, "directive_type": "no_op"}]},
            REQUEST,
        )
