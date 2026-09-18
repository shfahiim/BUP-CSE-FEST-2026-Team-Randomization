from __future__ import annotations

import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FiniteNonNegative = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
HourNumber = Annotated[int, Field(strict=True, ge=0, le=23)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class HourInput(RequestModel):
    hour: HourNumber
    demand_kwh: FiniteNonNegative
    solar_kwh: FiniteNonNegative
    tariff_bdt_per_kwh: FiniteNonNegative


class BatteryInput(RequestModel):
    capacity_kwh: FiniteNonNegative
    initial_energy_kwh: FiniteNonNegative
    minimum_energy_kwh: FiniteNonNegative
    max_charge_kwh_per_hour: FiniteNonNegative
    max_discharge_kwh_per_hour: FiniteNonNegative

    @model_validator(mode="after")
    def validate_energy_bounds(self) -> "BatteryInput":
        if self.minimum_energy_kwh > self.initial_energy_kwh:
            raise ValueError("minimum_energy_kwh must not exceed initial_energy_kwh")
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh must not exceed capacity_kwh")
        return self


class OptimizeRequest(RequestModel):
    scenario_id: Annotated[str, Field(min_length=1, max_length=256)]
    operator_notes: Annotated[list[str], Field(min_length=1, max_length=3)]
    hours: Annotated[list[HourInput], Field(min_length=24, max_length=24)]
    battery: BatteryInput

    @field_validator("scenario_id")
    @classmethod
    def scenario_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scenario_id must not be blank")
        return value

    @field_validator("operator_notes")
    @classmethod
    def notes_not_blank(cls, value: list[str]) -> list[str]:
        if any(not note.strip() for note in value):
            raise ValueError("operator_notes entries must not be blank")
        return value

    @field_validator("hours")
    @classmethod
    def exact_hour_set(cls, value: list[HourInput]) -> list[HourInput]:
        if sorted(item.hour for item in value) != list(range(24)):
            raise ValueError("hours must contain each integer from 0 through 23 exactly once")
        return value

    def hours_by_number(self) -> dict[int, HourInput]:
        return {item.hour: item for item in self.hours}


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HoursAdjustment(OutputModel):
    hours: list[HourNumber]


class SolarReductionAdjustment(HoursAdjustment):
    factor: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class ReserveAdjustment(HoursAdjustment):
    minimum_energy_kwh: FiniteNonNegative


class GridCapAdjustment(HoursAdjustment):
    max_grid_kwh: FiniteNonNegative


StructuredAdjustment = Union[
    SolarReductionAdjustment,
    ReserveAdjustment,
    GridCapAdjustment,
    HoursAdjustment,
    None,
]

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]


class DirectiveInterpretation(OutputModel):
    note_index: Annotated[int, Field(strict=True, ge=0)]
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: StructuredAdjustment
    explanation: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def validate_directive_shape(self) -> "DirectiveInterpretation":
        adjustment = self.structured_adjustment
        if self.directive_type == "no_op":
            if self.applies or adjustment is not None:
                raise ValueError("no_op requires applies=false and a null adjustment")
        elif not self.applies:
            raise ValueError("applicable directives require applies=true")
        elif self.directive_type == "solar_reduction" and not isinstance(
            adjustment, SolarReductionAdjustment
        ):
            raise ValueError("solar_reduction requires factor and hours")
        elif self.directive_type == "minimum_battery_reserve" and not isinstance(
            adjustment, ReserveAdjustment
        ):
            raise ValueError("minimum_battery_reserve requires minimum_energy_kwh and hours")
        elif self.directive_type == "max_grid_window" and not isinstance(
            adjustment, GridCapAdjustment
        ):
            raise ValueError("max_grid_window requires max_grid_kwh and hours")
        elif self.directive_type in {"no_charge_window", "no_discharge_window"} and type(
            adjustment
        ) is not HoursAdjustment:
            raise ValueError("charge/discharge windows require only hours")
        return self


class HourlyPlanEntry(OutputModel):
    hour: HourNumber
    grid_kwh: FiniteNonNegative
    solar_used_kwh: FiniteNonNegative
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: FiniteNonNegative
    battery_energy_after_kwh: FiniteNonNegative


class OptimizeResponse(OutputModel):
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: Annotated[list[HourlyPlanEntry], Field(min_length=24, max_length=24)]
    total_grid_kwh: FiniteNonNegative
    total_cost_bdt: FiniteNonNegative
    peak_grid_kwh: FiniteNonNegative
    plan_summary: str


class RawInterpretation(BaseModel):
    """Flat provider response; transformed into the public discriminated shape."""

    model_config = ConfigDict(extra="ignore")
    note_index: int | float
    directive_type: str
    hours: list[int | float] | None = None
    factor: float | None = None
    minimum_energy_kwh: float | None = None
    max_grid_kwh: float | None = None
    explanation: str = ""


class RawInterpretationEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    interpretations: list[RawInterpretation]


def finite_number(value: float | int | None) -> bool:
    return value is not None and not isinstance(value, bool) and math.isfinite(float(value))
