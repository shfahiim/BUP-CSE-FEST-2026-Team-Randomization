from __future__ import annotations

import math
from dataclasses import dataclass

from app.errors import DirectiveValidationError
from app.schemas import (
    DirectiveInterpretation,
    GridCapAdjustment,
    HoursAdjustment,
    OptimizeRequest,
    ReserveAdjustment,
    SolarReductionAdjustment,
)


@dataclass(frozen=True, slots=True)
class CompiledDirectives:
    effective_solar: tuple[float, ...]
    minimum_energy: tuple[float, ...]
    can_charge: tuple[bool, ...]
    can_discharge: tuple[bool, ...]
    maximum_grid: tuple[float | None, ...]


def compile_directives(
    request: OptimizeRequest, directives: list[DirectiveInterpretation]
) -> CompiledDirectives:
    by_hour = request.hours_by_number()
    capacity = request.battery.capacity_kwh
    factors = [1.0] * 24
    minimum_energy = [request.battery.minimum_energy_kwh] * 24
    can_charge = [True] * 24
    can_discharge = [True] * 24
    maximum_grid: list[float | None] = [None] * 24

    for directive in directives:
        kind = directive.directive_type
        adjustment = directive.structured_adjustment
        if kind == "no_op":
            if directive.applies or adjustment is not None:
                raise DirectiveValidationError("invalid no_op semantics")
            continue
        if not directive.applies or adjustment is None:
            raise DirectiveValidationError("applicable directive is missing its adjustment")
        if not isinstance(adjustment, HoursAdjustment) or not adjustment.hours:
            raise DirectiveValidationError("applicable directive requires non-empty hours")
        if adjustment.hours != sorted(set(adjustment.hours)):
            raise DirectiveValidationError("directive hours must be unique and ascending")

        for hour in adjustment.hours:
            if kind == "solar_reduction":
                if not isinstance(adjustment, SolarReductionAdjustment):
                    raise DirectiveValidationError("wrong solar_reduction adjustment shape")
                factors[hour] = min(factors[hour], adjustment.factor)
            elif kind == "minimum_battery_reserve":
                if not isinstance(adjustment, ReserveAdjustment):
                    raise DirectiveValidationError("wrong reserve adjustment shape")
                reserve = adjustment.minimum_energy_kwh
                if not math.isfinite(reserve) or not 0 <= reserve <= capacity:
                    raise DirectiveValidationError("reserve outside battery bounds")
                minimum_energy[hour] = max(minimum_energy[hour], reserve)
            elif kind == "no_charge_window":
                can_charge[hour] = False
            elif kind == "no_discharge_window":
                can_discharge[hour] = False
            elif kind == "max_grid_window":
                if not isinstance(adjustment, GridCapAdjustment):
                    raise DirectiveValidationError("wrong max_grid_window adjustment shape")
                cap = adjustment.max_grid_kwh
                if not math.isfinite(cap) or cap < 0:
                    raise DirectiveValidationError("invalid grid cap")
                previous = maximum_grid[hour]
                maximum_grid[hour] = cap if previous is None else min(previous, cap)
            else:
                raise DirectiveValidationError(f"unsupported directive type: {kind}")

    effective_solar = tuple(by_hour[h].solar_kwh * factors[h] for h in range(24))
    return CompiledDirectives(
        effective_solar=effective_solar,
        minimum_energy=tuple(minimum_energy),
        can_charge=tuple(can_charge),
        can_discharge=tuple(can_discharge),
        maximum_grid=tuple(maximum_grid),
    )
