from __future__ import annotations

import math

from app.directives import compile_directives
from app.schemas import DirectiveInterpretation, OptimizeRequest, OptimizeResponse

REPLAY_TOLERANCE = 1e-4


def replay_response(
    request: OptimizeRequest,
    directives: list[DirectiveInterpretation],
    response: OptimizeResponse,
) -> list[str]:
    errors: list[str] = []
    plan = response.hourly_plan
    if len(plan) != 24 or [item.hour for item in plan] != list(range(24)):
        return ["hourly_plan must contain hours 0 through 23 in order"]

    compiled = compile_directives(request, directives)
    by_hour = request.hours_by_number()
    battery = request.battery
    previous = battery.initial_energy_kwh

    for item in plan:
        hour = item.hour
        numeric = (
            item.grid_kwh,
            item.solar_used_kwh,
            item.battery_kwh,
            item.battery_energy_after_kwh,
        )
        if any(not math.isfinite(value) or value < -REPLAY_TOLERANCE for value in numeric):
            errors.append(f"hour {hour}: non-finite or negative output")
            continue
        charge = item.battery_kwh if item.battery_action == "charge" else 0.0
        discharge = item.battery_kwh if item.battery_action == "discharge" else 0.0
        if item.battery_action == "idle" and abs(item.battery_kwh) > REPLAY_TOLERANCE:
            errors.append(f"hour {hour}: idle action has non-zero magnitude")
        if item.solar_used_kwh > compiled.effective_solar[hour] + REPLAY_TOLERANCE:
            errors.append(f"hour {hour}: effective solar exceeded")
        if charge > battery.max_charge_kwh_per_hour + REPLAY_TOLERANCE:
            errors.append(f"hour {hour}: charge rate exceeded")
        if discharge > battery.max_discharge_kwh_per_hour + REPLAY_TOLERANCE:
            errors.append(f"hour {hour}: discharge rate exceeded")
        if charge > REPLAY_TOLERANCE and not compiled.can_charge[hour]:
            errors.append(f"hour {hour}: charged in no-charge window")
        if discharge > REPLAY_TOLERANCE and not compiled.can_discharge[hour]:
            errors.append(f"hour {hour}: discharged in no-discharge window")
        grid_cap = compiled.maximum_grid[hour]
        if grid_cap is not None and item.grid_kwh > grid_cap + REPLAY_TOLERANCE:
            errors.append(f"hour {hour}: grid cap exceeded")

        expected_energy = previous + charge - discharge
        if abs(item.battery_energy_after_kwh - expected_energy) > REPLAY_TOLERANCE:
            errors.append(f"hour {hour}: invalid battery transition")
        if item.battery_energy_after_kwh < compiled.minimum_energy[hour] - REPLAY_TOLERANCE:
            errors.append(f"hour {hour}: battery reserve violated")
        if item.battery_energy_after_kwh > battery.capacity_kwh + REPLAY_TOLERANCE:
            errors.append(f"hour {hour}: battery capacity exceeded")

        supplied = item.grid_kwh + item.solar_used_kwh + discharge
        consumed = by_hour[hour].demand_kwh + charge
        if abs(supplied - consumed) > REPLAY_TOLERANCE:
            errors.append(f"hour {hour}: energy balance violated")
        previous = item.battery_energy_after_kwh

    if abs(previous - battery.initial_energy_kwh) > REPLAY_TOLERANCE:
        errors.append("end-of-day battery neutrality violated")

    total_grid = sum(item.grid_kwh for item in plan)
    total_cost = sum(item.grid_kwh * by_hour[item.hour].tariff_bdt_per_kwh for item in plan)
    peak_grid = max(item.grid_kwh for item in plan)
    if abs(response.total_grid_kwh - total_grid) > REPLAY_TOLERANCE:
        errors.append("total_grid_kwh does not match hourly_plan")
    if abs(response.total_cost_bdt - total_cost) > REPLAY_TOLERANCE:
        errors.append("total_cost_bdt does not match hourly_plan")
    if abs(response.peak_grid_kwh - peak_grid) > REPLAY_TOLERANCE:
        errors.append("peak_grid_kwh does not match hourly_plan")
    return errors
