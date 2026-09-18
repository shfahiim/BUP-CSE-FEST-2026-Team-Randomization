from __future__ import annotations

from app.errors import DirectiveValidationError
from app.schemas import (
    DirectiveInterpretation,
    GridCapAdjustment,
    HoursAdjustment,
    OptimizeRequest,
    RawInterpretationEnvelope,
    ReserveAdjustment,
    SolarReductionAdjustment,
    finite_number,
)

SUPPORTED_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def _canonical_integer(value: int | float, field: str) -> int:
    if isinstance(value, bool) or not finite_number(value) or not float(value).is_integer():
        raise DirectiveValidationError(f"{field} must be an integer")
    return int(value)


def _canonical_hours(values: list[int | float] | None) -> list[int]:
    if not values:
        raise DirectiveValidationError("applicable directive hours must not be empty")
    hours = sorted({_canonical_integer(item, "hour") for item in values})
    if any(hour < 0 or hour > 23 for hour in hours):
        raise DirectiveValidationError("directive hours must be within 0 through 23")
    return hours


def validate_model_output(
    payload: object, request: OptimizeRequest
) -> list[DirectiveInterpretation]:
    try:
        envelope = RawInterpretationEnvelope.model_validate(payload)
    except Exception as exc:
        raise DirectiveValidationError("model output does not match the interpretation envelope") from exc

    expected_count = len(request.operator_notes)
    if len(envelope.interpretations) != expected_count:
        raise DirectiveValidationError("model must return exactly one entry per operator note")

    indexed: dict[int, object] = {}
    for raw in envelope.interpretations:
        index = _canonical_integer(raw.note_index, "note_index")
        if index in indexed:
            raise DirectiveValidationError("duplicate note_index")
        indexed[index] = raw
    if sorted(indexed) != list(range(expected_count)):
        raise DirectiveValidationError("note_index values must be exactly 0..N-1")

    capacity = request.battery.capacity_kwh
    result: list[DirectiveInterpretation] = []
    for index in range(expected_count):
        raw = indexed[index]
        directive_type = raw.directive_type
        if directive_type not in SUPPORTED_TYPES:
            raise DirectiveValidationError(f"unsupported directive_type for note {index}")
        explanation = raw.explanation.strip() or "Operator note interpreted for today's schedule."

        if directive_type == "no_op":
            result.append(
                DirectiveInterpretation(
                    note_index=index,
                    applies=False,
                    directive_type="no_op",
                    structured_adjustment=None,
                    explanation=explanation,
                )
            )
            continue

        hours = _canonical_hours(raw.hours)
        if directive_type == "solar_reduction":
            if not finite_number(raw.factor) or not 0 <= float(raw.factor) <= 1:
                raise DirectiveValidationError(f"invalid solar factor for note {index}")
            adjustment = SolarReductionAdjustment(hours=hours, factor=float(raw.factor))
        elif directive_type == "minimum_battery_reserve":
            reserve = raw.minimum_energy_kwh
            if not finite_number(reserve) or not 0 <= float(reserve) <= capacity:
                raise DirectiveValidationError(f"invalid battery reserve for note {index}")
            adjustment = ReserveAdjustment(hours=hours, minimum_energy_kwh=float(reserve))
        elif directive_type == "max_grid_window":
            cap = raw.max_grid_kwh
            if not finite_number(cap) or float(cap) < 0:
                raise DirectiveValidationError(f"invalid grid cap for note {index}")
            adjustment = GridCapAdjustment(hours=hours, max_grid_kwh=float(cap))
        else:
            adjustment = HoursAdjustment(hours=hours)

        result.append(
            DirectiveInterpretation(
                note_index=index,
                applies=True,
                directive_type=directive_type,
                structured_adjustment=adjustment,
                explanation=explanation,
            )
        )
    return result


def as_no_op(note_index: int, reason: str) -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=note_index,
        applies=False,
        directive_type="no_op",
        structured_adjustment=None,
        explanation=reason,
    )


def all_no_ops(request: OptimizeRequest, reason: str) -> list[DirectiveInterpretation]:
    return [as_no_op(index, reason) for index in range(len(request.operator_notes))]
