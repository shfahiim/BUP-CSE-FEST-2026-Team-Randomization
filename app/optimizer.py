from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

from app.directives import compile_directives
from app.errors import OptimizationError, OptimizationInfeasible
from app.schemas import DirectiveInterpretation, HourlyPlanEntry, OptimizeRequest

HOURS = 24
GRID, SOLAR, CHARGE, DISCHARGE, ENERGY = 0, HOURS, 2 * HOURS, 3 * HOURS, 4 * HOURS
SOLVER_TOL = 1e-8


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    plan: list[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float


def _solve_raw(request: OptimizeRequest, directives: list[DirectiveInterpretation]):
    compiled = compile_directives(request, directives)
    by_hour = request.hours_by_number()
    battery = request.battery
    count = 5 * HOURS

    objective = np.zeros(count)
    for hour in range(HOURS):
        objective[GRID + hour] = by_hour[hour].tariff_bdt_per_kwh

    lower = np.zeros(count)
    upper = np.full(count, np.inf)
    for hour in range(HOURS):
        upper[SOLAR + hour] = compiled.effective_solar[hour]
        upper[CHARGE + hour] = (
            battery.max_charge_kwh_per_hour if compiled.can_charge[hour] else 0.0
        )
        upper[DISCHARGE + hour] = (
            battery.max_discharge_kwh_per_hour if compiled.can_discharge[hour] else 0.0
        )
        lower[ENERGY + hour] = compiled.minimum_energy[hour]
        upper[ENERGY + hour] = battery.capacity_kwh
        if compiled.maximum_grid[hour] is not None:
            upper[GRID + hour] = compiled.maximum_grid[hour]
    lower[ENERGY + 23] = upper[ENERGY + 23] = battery.initial_energy_kwh

    rows: list[np.ndarray] = []
    rhs: list[float] = []
    for hour in range(HOURS):
        row = np.zeros(count)
        row[GRID + hour] = 1
        row[SOLAR + hour] = 1
        row[DISCHARGE + hour] = 1
        row[CHARGE + hour] = -1
        rows.append(row)
        rhs.append(by_hour[hour].demand_kwh)

    for hour in range(HOURS):
        row = np.zeros(count)
        row[ENERGY + hour] = 1
        row[CHARGE + hour] = -1
        row[DISCHARGE + hour] = 1
        if hour:
            row[ENERGY + hour - 1] = -1
            rhs.append(0.0)
        else:
            rhs.append(battery.initial_energy_kwh)
        rows.append(row)

    return linprog(
        objective,
        A_eq=np.asarray(rows),
        b_eq=np.asarray(rhs),
        bounds=list(zip(lower, upper)),
        method="highs",
    )


def solve(
    request: OptimizeRequest,
    directives: list[DirectiveInterpretation],
    decimal_places: int = 6,
) -> OptimizationResult:
    result = _solve_raw(request, directives)
    if result.status == 2:
        raise OptimizationInfeasible("interpreted directives make the schedule infeasible")
    if not result.success or result.x is None:
        raise OptimizationError(f"solver failed with status {result.status}")
    return _serialize(request, result.x, decimal_places)


def _serialize(request: OptimizeRequest, values: np.ndarray, places: int) -> OptimizationResult:
    by_hour = request.hours_by_number()
    plan: list[HourlyPlanEntry] = []
    energy = request.battery.initial_energy_kwh
    for hour in range(HOURS):
        net = float(values[CHARGE + hour] - values[DISCHARGE + hour])
        if net > SOLVER_TOL:
            action, magnitude = "charge", round(net, places)
        elif net < -SOLVER_TOL:
            action, magnitude = "discharge", round(-net, places)
        else:
            action, magnitude = "idle", 0.0
        if magnitude == 0:
            action = "idle"
        if action == "charge":
            energy += magnitude
        elif action == "discharge":
            energy -= magnitude
        energy = round(energy, places)
        plan.append(
            HourlyPlanEntry(
                hour=hour,
                grid_kwh=round(max(float(values[GRID + hour]), 0.0), places),
                solar_used_kwh=round(max(float(values[SOLAR + hour]), 0.0), places),
                battery_action=action,
                battery_kwh=magnitude,
                battery_energy_after_kwh=energy,
            )
        )

    total_grid = round(sum(item.grid_kwh for item in plan), places)
    total_cost = round(
        sum(item.grid_kwh * by_hour[item.hour].tariff_bdt_per_kwh for item in plan), places
    )
    peak_grid = round(max(item.grid_kwh for item in plan), places)
    return OptimizationResult(plan, total_grid, total_cost, peak_grid)


def is_feasible(request: OptimizeRequest, directives: list[DirectiveInterpretation]) -> bool:
    try:
        result = _solve_raw(request, directives)
    except Exception:
        return False
    return bool(result.success)


def minimal_infeasible_note_indices(
    request: OptimizeRequest, directives: list[DirectiveInterpretation]
) -> list[int]:
    active = [item for item in directives if item.directive_type != "no_op"]
    for size in range(1, len(active) + 1):
        for subset in itertools.combinations(active, size):
            if not is_feasible(request, list(subset)):
                return [item.note_index for item in subset]
    return [item.note_index for item in active]


def largest_feasible_note_indices(
    request: OptimizeRequest, directives: list[DirectiveInterpretation]
) -> list[int]:
    active = [item for item in directives if item.directive_type != "no_op"]
    for size in range(len(active), -1, -1):
        for subset in itertools.combinations(active, size):
            if is_feasible(request, list(subset)):
                return [item.note_index for item in subset]
    raise OptimizationError("base scenario is unexpectedly infeasible")
