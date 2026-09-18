"""Self-checking offline validation of the GridWise model against the public sample pack.

Every claim plan.md makes about the model is asserted here. Exits non-zero on any failure,
so it can gate a commit.

Checks:
  1. A pure LP reaches the published reference optimum on all 10 cases.
  2. Our replay rules accept all 10 published reference schedules.
  3. Published totals are recomputable from the published plans.
  4. Our own LP output survives netting, serialization, replay, and totals recomputation.
  5. net_out preserves balance, battery trajectory, and rate limits on adversarial input.
  6. No simultaneous charge/discharge appears on original, flat, and zero tariff profiles.
  7. Overlapping solar_reduction uses lowest-factor semantics (not multiplication).
  8. Guardrail-level directive errors are raised, not silently clamped.
  9. Known infeasibility triggers are detected and the feasible-subset fallback recovers.

Run: python3 reference_check.py
"""
import copy
import itertools
import json
import math
import sys

import numpy as np
from scipy.optimize import linprog

H = 24
TOL = 1e-6
PLACES = 6
CASES = json.load(open("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"))["cases"]

# Variable block offsets in the LP vector.
G, S, C, D, E = 0, H, 2 * H, 3 * H, 4 * H

_failures = []


def check(name, ok, detail=""):
    if not ok:
        _failures.append(f"{name}: {detail}")
    print(f"  [{'pass' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


class DirectiveError(ValueError):
    """A directive that guardrails must reject rather than repair."""


def compile_directives(inp, directives):
    """Compile validated directives into per-hour arrays.

    Raises DirectiveError instead of clamping: a reserve above capacity is a guardrail
    failure, and silently repairing it would make the response contradict itself.
    """
    hours = {h["hour"]: h for h in inp["hours"]}
    cap = float(inp["battery"]["capacity_kwh"])
    eff_solar = [float(hours[h]["solar_kwh"]) for h in range(H)]
    factor = [1.0] * H  # tracked separately so overlaps take the lowest, not the product
    min_e = [float(inp["battery"]["minimum_energy_kwh"])] * H
    can_charge = [True] * H
    can_discharge = [True] * H
    max_grid = [None] * H

    for d in directives:
        t, a = d["directive_type"], d["structured_adjustment"]
        if t == "no_op":
            if a is not None:
                raise DirectiveError("no_op carries a non-null adjustment")
            continue
        hrs = a["hours"]
        if not hrs or sorted(set(hrs)) != list(hrs) or any(h not in range(H) for h in hrs):
            raise DirectiveError(f"{t}: hours must be non-empty, unique, ascending, 0-23")
        for h in hrs:
            if t == "solar_reduction":
                f = a["factor"]
                if not math.isfinite(f) or not 0.0 <= f <= 1.0:
                    raise DirectiveError(f"factor {f} outside [0,1]")
                factor[h] = min(factor[h], f)  # lowest remaining factor wins
            elif t == "minimum_battery_reserve":
                r = a["minimum_energy_kwh"]
                if not math.isfinite(r) or r < 0 or r > cap:
                    raise DirectiveError(f"reserve {r} outside [0, capacity={cap}]")
                min_e[h] = max(min_e[h], r)
            elif t == "no_charge_window":
                can_charge[h] = False
            elif t == "no_discharge_window":
                can_discharge[h] = False
            elif t == "max_grid_window":
                g = a["max_grid_kwh"]
                if not math.isfinite(g) or g < 0:
                    raise DirectiveError(f"grid cap {g} negative")
                max_grid[h] = g if max_grid[h] is None else min(max_grid[h], g)
            else:
                raise DirectiveError(f"unsupported directive_type {t}")

    eff_solar = [eff_solar[h] * factor[h] for h in range(H)]
    return eff_solar, min_e, can_charge, can_discharge, max_grid


def solve(inp, directives):
    hours = {h["hour"]: h for h in inp["hours"]}
    b = inp["battery"]
    cap, e0 = float(b["capacity_kwh"]), float(b["initial_energy_kwh"])
    mc, md = float(b["max_charge_kwh_per_hour"]), float(b["max_discharge_kwh_per_hour"])
    eff_solar, min_e, can_charge, can_discharge, max_grid = compile_directives(inp, directives)

    n = 5 * H
    c = np.zeros(n)
    for h in range(H):
        c[G + h] = float(hours[h]["tariff_bdt_per_kwh"])

    lb, ub = np.zeros(n), np.full(n, np.inf)
    for h in range(H):
        ub[S + h] = eff_solar[h]
        ub[C + h] = mc if can_charge[h] else 0.0
        ub[D + h] = md if can_discharge[h] else 0.0
        lb[E + h], ub[E + h] = min_e[h], cap
        if max_grid[h] is not None:
            ub[G + h] = max_grid[h]
    lb[E + H - 1] = ub[E + H - 1] = e0  # end-of-day neutrality

    rows, rhs = [], []
    for h in range(H):  # grid + solar_used + discharge - charge = demand
        r = np.zeros(n)
        r[G + h] = r[S + h] = r[D + h] = 1
        r[C + h] = -1
        rows.append(r)
        rhs.append(float(hours[h]["demand_kwh"]))
    for h in range(H):  # E[h] = E[h-1] + charge - discharge
        r = np.zeros(n)
        r[E + h], r[C + h], r[D + h] = 1, -1, 1
        if h:
            r[E + h - 1] = -1
            rhs.append(0.0)
        else:
            rhs.append(e0)
        rows.append(r)

    return linprog(c, A_eq=np.array(rows), b_eq=np.array(rhs),
                   bounds=list(zip(lb, ub)), method="highs")


def net_out(charge, discharge):
    """Collapse any simultaneous charge/discharge into one action per hour."""
    actions = []
    for c_h, d_h in zip(charge, discharge):
        net = c_h - d_h
        if net > TOL:
            actions.append(("charge", net))
        elif net < -TOL:
            actions.append(("discharge", -net))
        else:
            actions.append(("idle", 0.0))
    return actions


def serialize(inp, res):
    """Turn an LP solution into the exact response plan, rounded as the judge will see it."""
    e0 = float(inp["battery"]["initial_energy_kwh"])
    x = res.x
    actions = net_out(x[C:C + H], x[D:D + H])
    plan, energy = [], e0
    for h in range(H):
        act, mag = actions[h]
        mag = round(mag, PLACES)
        energy = round(energy + (mag if act == "charge" else -mag if act == "discharge" else 0.0), PLACES)
        plan.append({
            "hour": h,
            "grid_kwh": round(max(x[G + h], 0.0), PLACES),
            "solar_used_kwh": round(max(x[S + h], 0.0), PLACES),
            "battery_action": act,
            "battery_kwh": mag,
            "battery_energy_after_kwh": energy,
        })
    return plan


def totals(inp, plan):
    hours = {h["hour"]: h for h in inp["hours"]}
    grid = sum(p["grid_kwh"] for p in plan)
    cost = sum(p["grid_kwh"] * float(hours[p["hour"]]["tariff_bdt_per_kwh"]) for p in plan)
    return round(grid, PLACES), round(cost, PLACES), round(max(p["grid_kwh"] for p in plan), PLACES)


def replay(inp, directives, plan):
    """Independent validation of a plan. Returns a list of violations."""
    v = []
    hours = {h["hour"]: h for h in inp["hours"]}
    b = inp["battery"]
    cap, e0 = float(b["capacity_kwh"]), float(b["initial_energy_kwh"])
    mc, md = float(b["max_charge_kwh_per_hour"]), float(b["max_discharge_kwh_per_hour"])
    eff_solar, min_e, can_charge, can_discharge, max_grid = compile_directives(inp, directives)

    if sorted(p["hour"] for p in plan) != list(range(H)):
        return ["hourly_plan hours are not exactly 0..23"]
    by_h = {p["hour"]: p for p in plan}
    prev = e0
    for h in range(H):
        p = by_h[h]
        g, s = float(p["grid_kwh"]), float(p["solar_used_kwh"])
        act, bk = p["battery_action"], float(p["battery_kwh"])
        ea = float(p["battery_energy_after_kwh"])
        chg = bk if act == "charge" else 0.0
        dis = bk if act == "discharge" else 0.0
        if min(g, s, bk) < -TOL:
            v.append(f"h{h}: negative value")
        if act not in ("charge", "discharge", "idle"):
            v.append(f"h{h}: bad battery_action {act!r}")
        if act == "idle" and abs(bk) > TOL:
            v.append(f"h{h}: idle with battery_kwh={bk}")
        if s > eff_solar[h] + 0.01:
            v.append(f"h{h}: solar_used {s} > effective solar {eff_solar[h]}")
        if chg > mc + 0.01 or dis > md + 0.01:
            v.append(f"h{h}: rate limit")
        if chg > TOL and not can_charge[h]:
            v.append(f"h{h}: charge inside no_charge_window")
        if dis > TOL and not can_discharge[h]:
            v.append(f"h{h}: discharge inside no_discharge_window")
        if max_grid[h] is not None and g > max_grid[h] + 0.01:
            v.append(f"h{h}: grid {g} > cap {max_grid[h]}")
        if abs(ea - (prev + chg - dis)) > 0.01:
            v.append(f"h{h}: battery transition")
        if ea < min_e[h] - 0.01 or ea > cap + 0.01:
            v.append(f"h{h}: energy {ea} outside [{min_e[h]}, {cap}]")
        if abs(g + s + dis - (float(hours[h]["demand_kwh"]) + chg)) > 0.01:
            v.append(f"h{h}: energy balance")
        prev = ea
    if abs(prev - e0) > 0.01:
        v.append(f"final energy {prev} != initial {e0}")
    return v


def largest_feasible_subset(inp, interp):
    """Emergency fallback: keep the most directives that are jointly feasible.

    Returns (kept_note_indices, result). With at most 3 notes this is at most 8 solves of a
    millisecond LP, so exhaustive search is affordable and deterministic.
    """
    applying = [d for d in interp if d["directive_type"] != "no_op"]
    for size in range(len(applying), -1, -1):
        for subset in itertools.combinations(applying, size):
            try:
                res = solve(inp, list(subset))
            except DirectiveError:
                continue
            if res.status == 0:
                return [d["note_index"] for d in subset], res
    return None, None


def synthetic(directive_type, **adj):
    return {"note_index": 0, "applies": directive_type != "no_op",
            "directive_type": directive_type,
            "structured_adjustment": None if directive_type == "no_op" else adj,
            "explanation": "synthetic"}


def main():
    print("1-4. LP optimality, reference replay, published totals, our own round trip")
    for case in CASES:
        inp, out = case["input"], case["expected_output"]
        interp = out["directive_interpretation"]
        res = solve(inp, interp)
        gap = abs(res.fun - out["total_cost_bdt"])
        ref_viol = replay(inp, interp, out["hourly_plan"])
        ref_tot = totals(inp, out["hourly_plan"])
        published = (out["total_grid_kwh"], out["total_cost_bdt"], out["peak_grid_kwh"])
        plan = serialize(inp, res)
        our_viol = replay(inp, interp, plan)
        our_tot = totals(inp, plan)
        ok = (gap <= 0.01 and not ref_viol and not our_viol
              and all(abs(a - b) <= 0.01 for a, b in zip(ref_tot, published))
              and abs(our_tot[1] - out["total_cost_bdt"]) <= 0.01)
        check(case["id"], ok,
              f"gap={gap:.4f} ref_replay={ref_viol} our_replay={our_viol} "
              f"ref_totals={ref_tot} published={published} our_totals={our_tot}")

    print("\n5. net_out invariants on adversarial simultaneous charge/discharge")
    charge = [10.0, 30.0, 5.0, 0.0]
    discharge = [30.0, 10.0, 5.0, 0.0]
    acts = net_out(charge, discharge)
    deltas_ok = all(
        abs(({"charge": 1, "discharge": -1, "idle": 0}[a] * m) - (c - d)) < TOL
        for (a, m), c, d in zip(acts, charge, discharge))
    check("net delta equals charge - discharge", deltas_ok, str(acts))
    check("netted magnitude never exceeds the larger original",
          all(m <= max(c, d) + TOL for (a, m), c, d in zip(acts, charge, discharge)), str(acts))
    check("equal charge and discharge becomes idle", acts[2] == ("idle", 0.0), str(acts[2]))

    print("\n6. netting is required, and cost survives it, across tariff profiles")
    # The solver DOES return simultaneous charge/discharge on degenerate (flat/zero) tariffs,
    # so net_out is load-bearing rather than defensive. What must hold is that netting changes
    # neither validity nor cost.
    for label, mutate in [
        ("published tariffs", lambda hs: hs),
        ("flat tariff 10", lambda hs: [dict(h, tariff_bdt_per_kwh=10) for h in hs]),
        ("all-zero tariff", lambda hs: [dict(h, tariff_bdt_per_kwh=0) for h in hs]),
    ]:
        needed_netting, broken = [], []
        for case in CASES:
            inp = copy.deepcopy(case["input"])
            inp["hours"] = mutate(inp["hours"])
            interp = case["expected_output"]["directive_interpretation"]
            res = solve(inp, interp)
            if res.status != 0:
                broken.append(f"{case['id']} solve status={res.status}")
                continue
            both = [h for h in range(H) if res.x[C + h] > TOL and res.x[D + h] > TOL]
            if both:
                needed_netting.append(f"{case['id']}{both}")
            plan = serialize(inp, res)
            viol = replay(inp, interp, plan)
            if viol:
                broken.append(f"{case['id']} replay={viol}")
            if abs(totals(inp, plan)[1] - res.fun) > 0.01:
                broken.append(f"{case['id']} cost drifted {totals(inp, plan)[1]} vs {res.fun}")
        check(f"{label}: netted plans replay clean at unchanged cost", not broken, str(broken))
        print(f"         netting was required for: {needed_netting or 'no case'}")

    print("\n7. overlapping solar_reduction uses lowest factor, not the product")
    inp = copy.deepcopy(CASES[0]["input"])
    base = float({h["hour"]: h for h in inp["hours"]}[12]["solar_kwh"])
    eff = compile_directives(inp, [synthetic("solar_reduction", hours=[12], factor=0.5),
                                   synthetic("solar_reduction", hours=[12], factor=0.4)])[0]
    check("two overlapping factors 0.5 and 0.4 give 0.4x", abs(eff[12] - base * 0.4) < TOL,
          f"expected {base * 0.4}, got {eff[12]} (product would be {base * 0.2})")

    print("\n8. guardrail-level directive errors are raised, not clamped")
    for label, directive in [
        ("reserve above capacity", synthetic("minimum_battery_reserve", hours=[18], minimum_energy_kwh=9e9)),
        ("factor above 1", synthetic("solar_reduction", hours=[12], factor=1.5)),
        ("negative grid cap", synthetic("max_grid_window", hours=[18], max_grid_kwh=-5)),
        ("non-finite reserve", synthetic("minimum_battery_reserve", hours=[18], minimum_energy_kwh=float("nan"))),
        ("non-finite factor", synthetic("solar_reduction", hours=[12], factor=float("inf"))),
        ("non-finite grid cap", synthetic("max_grid_window", hours=[18], max_grid_kwh=float("nan"))),
        ("unsorted hours", synthetic("no_charge_window", hours=[14, 13])),
        ("empty hours", synthetic("no_charge_window", hours=[])),
        ("hour out of range", synthetic("no_charge_window", hours=[24])),
    ]:
        raised = False
        try:
            compile_directives(CASES[0]["input"], [directive])
        except DirectiveError:
            raised = True
        check(f"rejects {label}", raised)

    print("\n9. infeasibility triggers are detected and the fallback recovers")
    probes = [
        ("grid cap far below demand", [synthetic("max_grid_window", hours=[18, 19, 20], max_grid_kwh=20)]),
        ("reserve at hour 23 above initial energy",
         [synthetic("minimum_battery_reserve", hours=[22, 23], minimum_energy_kwh=200)]),
        ("all-day no-charge plus high reserve",
         [synthetic("no_charge_window", hours=list(range(H))),
          dict(synthetic("minimum_battery_reserve", hours=[20], minimum_energy_kwh=200), note_index=1)]),
    ]
    inp = CASES[4]["input"]  # SAMPLE-05, initial energy 120, capacity 240
    for label, directives in probes:
        res = solve(inp, directives)
        kept, fb = largest_feasible_subset(inp, directives)
        recovered = fb is not None and fb.status == 0 and not replay(
            inp, [d for d in directives if d["note_index"] in kept], serialize(inp, fb))
        check(f"{label}: infeasible then recovered", res.status != 0 and recovered,
              f"solve status={res.status} kept={kept}")

    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
