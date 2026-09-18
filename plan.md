# GridWise Hackathon — Analysis and Execution Plan

Revision 2. Rewritten after reading both PDFs line by line and machine-verifying the
sample pack with `reference_check.py`. Section 1 lists what changed from the first draft;
the rest is the implementation reference.

## 1. Corrections and additions to the first draft

Ten changes. The first three are the ones that would most likely have cost us points.

### 1.1 A provider failure should degrade to a valid 200, not a 500

The draft said that if the model output stays invalid after one retry, we should return a
sanitized server error instead of degrading a note to `no_op`. That is the wrong trade for a
*provider or interpretation* failure. Compare the two outcomes for one hidden case:

| Outcome | What we lose |
|---|---|
| HTTP 500 | The whole case (interpretation, application, optimization) **plus** failure-rate points under Performance & Reliability, which explicitly penalizes 5xx on valid requests. |
| Degrade the note to `no_op` and still return a valid 24-hour schedule | Interpretation credit for that one note, and directive-application credit for that case. We keep the schema points, the energy-balance/battery/neutrality validity checks, the other notes in the same request, and the reliability points. |

So the three-way rule is:

- **Valid request, provider or interpretation failure** → degraded but schema-valid and physically valid 200.
- **Malformed or structurally invalid request** → 400.
- **Unexpected internal inconsistency where we cannot vouch for correctness** → sanitized 500.

The third branch matters: "always 200" is a target, not a promise, and it must never be met by
fabricating plan numbers. A 500 is the correct answer when replay of our own output fails for a
reason we do not understand, because emitting a schedule we know is wrong is worse than
admitting the failure. Every 500 seen in testing is a defect to fix, not an accepted path.

### 1.2 A pure LP is enough — drop the 24-binary MILP

The draft specified a MILP with a binary charge-mode variable per hour to prevent
simultaneous charge and discharge. Verified with `reference_check.py`: a plain LP over
`grid`, `solar_used`, `charge`, `discharge`, `energy_after` hits the published optimum on
all 10 cases to 0.000000 BDT.

**Netting is mandatory, not defensive.** On flat and all-zero tariff profiles HiGHS returns
hours with both charge and discharge non-zero in three of the ten cases — SAMPLE-05, -07 and
-10, i.e. exactly the ones carrying grid caps and reserves, where degenerate optima abound. So
the post-solve net-out is load-bearing and must be covered by tests, not treated as a
theoretical safeguard. It is provably safe:

- `E` depends only on `charge − discharge`, so netting preserves the whole battery trajectory.
- The balance equation contains `discharge − charge`, so it is preserved too.
- The netted magnitude never exceeds the larger of the two, so rate limits still hold.
- Prohibition windows already force the relevant term to 0, so netting cannot enter a window.

`reference_check.py` asserts all four invariants directly, and separately asserts that netted,
serialized plans still replay clean at unchanged cost on all three tariff profiles.

This removes integer solving from the latency budget and removes a class of MILP bugs. Keep
the MILP formulation on paper only as a contingency if some hidden case somehow needs it.

### 1.3 Infeasibility needs a re-interpretation ladder, and the response must never contradict itself

The draft treated infeasibility as an internal error. But infeasibility is reachable from a
*single* wrong number in an otherwise plausible interpretation, and `reference_check.py`
confirms all three of these go infeasible in HiGHS:

- a `max_grid_window` cap below what demand minus battery can cover;
- a `minimum_battery_reserve` covering hour 23 above `initial_energy_kwh`, which fights end-of-day neutrality;
- an all-day `no_charge_window` combined with a reserve above the starting energy.

Organizers guarantee their *valid scoring* scenarios are feasible under ground truth, so
infeasibility is our signal that *we* misread a note. The right response is therefore to try
harder to read it correctly, not to quietly weaken it:

1. Validated interpretation, feasible → use it exactly.
2. Invalid or infeasible → retry with diagnostics identifying the invalid note or minimal
   jointly infeasible note set. Request the complete ordered batch again so note mapping remains exact.
3. Still failing → the backup genuine LLM, same schema.
4. Still failing → keep the largest jointly feasible subset of directives, and report every discarded note as `no_op`.
5. Unexpected internal inconsistency → sanitized 500.

If every model attempt fails before producing any parseable interpretation, emit one `no_op`
entry per input note, solve the base scenario, and return it only if independent replay passes.
This is an emergency reliability path, not a replacement for mandatory LLM interpretation.

**The self-consistency rule that makes rung 4 safe.** Anything the optimizer used must exactly
match what `directive_interpretation` reports. Two consequences:

- **No silent clamping.** The draft's "raise the grid cap to the hour's demand" rung is deleted. Reporting `max_grid_kwh: 100` while solving against 180 makes the response contradict itself, and a value we invented is not the value the note stated either way. A reserve above capacity is a guardrail rejection, not a repair — `compile_directives` now raises instead of clamping.
- **Dropped means `no_op`.** If rung 4 discards a directive, that note is reported as `no_op` with a null adjustment, so replaying our plan against our own reported interpretation still passes.

This costs us the relevance point for that note, which is the honest price. The judge replays
using "the true hidden directive, not only the team-reported interpretation" — the phrase
*not only* implies both checks may run, so a plan that violates its own stated directive is
exposed either way.

Rung 4 is a largest-feasible-subset search, which is affordable precisely because a scenario
has at most 3 notes: at most 8 subsets of a millisecond LP, enumerated largest-first in a fixed
order so the result is deterministic. `reference_check.py` exercises it against all three
infeasibility probes above.

Never relax the physics: energy balance, battery bounds, rate limits, and neutrality stay hard
at every rung. Record which rung produced the answer so it shows up in testing.

### 1.4 Be lenient about unknown request fields (was: reject them)

The draft suggested rejecting unknown top-level fields "to keep the contract exact." That is
an unforced risk: if the harness adds a metadata field, strict mode turns every hidden case
into a 400. Be strict about the fields the spec defines (types, ranges, exactly 24 hours,
1–3 notes) and silently ignore anything extra.

### 1.5 FastAPI's default validation status is 422, but the spec wants 400

The spec assigns 400 to "malformed JSON or structurally invalid request" and makes 422
optional. Pydantic/FastAPI returns 422 for both by default, so we must install a
`RequestValidationError` handler that maps schema failures to 400. The draft named the right
codes but not this trap, and 2 of the 10 API points are request validation.

### 1.6 Canonicalize model output where meaning is preserved; retry only for real breaks

The draft refused to sort or coerce, on the grounds that coercion could mask a weak
interpretation. But sorting `[14,13]` to `[13,14]`, de-duplicating repeats, and casting
`13.0` to `13` change nothing semantically — they are exactly the normalization the guardrail
section asks us to guarantee on output. Reserve the retry for breaks that do change meaning
or cannot be repaired: unknown `directive_type`, wrong adjustment shape, hours outside 0–23,
`note_index` that does not line up, factor outside `[0,1]` beyond a rounding whisker, non-finite
numbers. Then the ladder in 1.1 catches whatever survives.

### 1.7 Midnight-wrapping and all-day windows were missing

Nothing in the draft handled "from 10 PM until 2 AM." With ascending-order hours required,
that is `[0, 1, 22, 23]`, not `[22, 23, 0, 1]`. Also missing: "all day" / "throughout the day"
maps to all 24 hours, and a directive must never come back with an empty `hours` array. Both
need an explicit prompt rule and a test.

### 1.8 The `no_op` decision is about time scope, not topic

All four public distractors are off-topic campus chatter (cafeteria menu, library hours,
seminar booking), which makes topic detection look sufficient. It is not — hidden distractors
will very likely be energy-flavoured but out of scope: "tomorrow's solar forecast is poor,"
"the charger will be serviced next month," "last week we exceeded the feeder limit." The
prompt must ask one question per note: does this change *today's* 24-hour schedule? Past,
future-dated, and advisory notes are `no_op` even when they are entirely about energy.

### 1.9 The relevance asymmetry is real, but it is not a licence to invent directives

Falling out of the penalty table: a missed real directive makes the plan violate ground truth,
which invalidates the case and zeroes its optimization credit. A spurious extra directive
usually only shrinks our feasible set, so the plan stays valid under ground truth and we lose a
slice of the 10 optimization points plus one relevance point. Under-application is the more
expensive error, so the interpreter should not be tuned to be timid.

But the asymmetry holds *only while the problem stays feasible*, and a hallucinated reserve or
grid cap is exactly what makes it infeasible — dropping us into the ladder in 1.3, where we lose
more than the slice we were protecting. So the instruction to the model is evidence-based rather
than strategic:

> Apply a directive when the note gives reasonable semantic evidence for it. Do not default
> uncertain or out-of-scope notes to directives as a scoring tactic.

The bias belongs in how firmly the prompt handles genuinely ambiguous *scope* wording, not in a
standing preference for emitting constraints.

### 1.10 Deployment: one artifact, no cold starts, and early external verification

Two process fixes. First, the draft treated the hosted deployment and the Docker fallback as
separate phases; they should be the *same image* — build once, push to a registry, deploy
that exact tag. Verifying one verifies most of the other, and it removes the "works hosted,
broken in Docker" failure. Second, free tiers that sleep (Render free, similar) will blow the
60-second readiness window or add cold-start latency to p95. Use a platform that keeps one
instance warm and verify the public deployment before polishing secondary artifacts.

## 2. What the documents actually require

Three sources, with a stated precedence: the **Problem Statement** is canonical for endpoints,
schemas, directives, guardrails, battery rules, and validity. The **Participant Guide** is
canonical for deployment, submission, scoring, and tie-breaks. The **sample pack** is worked
examples, explicitly not the judge set.

### Scoring, and what it implies

| Category | Points | Implication |
|---|---:|---|
| LLM directive interpretation | 25 | 5 relevance + 5 type + 5 hours + 5 numerics/shape + 5 paraphrase robustness. Explanation text is not matched. |
| Directive application & constraint correctness | 25 | 10 ground-truth application + 5 balance/effective-solar + 5 battery transitions/bounds/rates + 5 action consistency/neutrality/non-negativity. |
| Optimization quality | 10 | `10 × avg(min(1, organizer_optimal / our_cost))`. Invalid cases score zero here. |
| API contract & schema | 10 | 2 endpoints/status + 2 request validation + 3 interpretation schema/order + 3 plan/top-level schema and `scenario_id` echo. |
| Performance & reliability | 10 | 2 health readiness + 3 p95 latency + 3 stability/failure rate + 2 controlled failure handling and secret safety. |
| Deployment & Docker fallback | 10 | 3 live reachability + 4 pullable image reaching `/health` + 2 clean startup + 1 no judge debugging. |
| Documentation & local reproducibility | 10 | 3 clean quickstart + 2 env/model docs + 2 sample-test procedure + 1 architecture explanation + 1 Docker instructions + 1 dependencies/limitations/secrets. |

The video carries no base points and is only the first tie-breaker, ahead of directive
application, interpretation, optimization, schema, reliability, and documentation in that order.

Half the score is language understanding plus applying what we understood. Optimization is
only 10 points, and Section 3 shows those 10 are nearly free once interpretation is right —
so effort belongs in interpretation and validity, not in solver sophistication.

### Disqualifying and hard requirements

- An LLM must produce the structured interpretation that feeds the optimizer. Regex-only interpretation, or an LLM used only for `plan_summary`/docs, fails the mandatory requirement and forfeits shortlist eligibility. Judges may inspect the repo to confirm.
- `GET /health` returns exactly `{"status":"ok"}` and must be ready within 60 s of start.
- `POST /optimize-energy` must complete within 30 s. p95 ≤ 5 s scores 3/3, >5–15 s scores 2/3, >15–30 s scores 1/3.
- Numeric tolerance is absolute 0.01 kWh / 0.01 BDT.
- New repo created after question reveal, private during the event, public after the deadline.
- No secrets in the repo, logs, responses, or image.
- Docker image must be pullable by exact tag/digest, bind `0.0.0.0`, expose the documented port, and contain no baked-in credentials.

## 3. Verified facts from the sample pack

`reference_check.py` asserts every claim below and exits non-zero if any of them stops holding,
so it can gate a commit. Rerun it any time we touch the model.

1. **A pure LP matches every published optimum exactly** — all 10 cases, worst gap 0.000000 BDT against a 0.01 tolerance. Optimization points are effectively guaranteed whenever our interpretation matches ground truth, which is another reason to spend the night on interpretation.
2. **Our replay rules accept all 10 reference schedules with zero violations**, so our reading of balance, bounds, rates, transitions, and neutrality matches the organizers'.
3. **Reported totals in the pack are exactly recomputable** from the hourly plans — `total_grid_kwh`, `total_cost_bdt`, and `peak_grid_kwh` all checked — confirming the plan is the source of truth for totals.
4. **Our own output survives the full round trip**: solve → net → round to 6 places → replay → recompute totals, with cost unchanged against the LP objective.
5. **Netting is required in practice, not just in theory** (see 1.2): three cases produce simultaneous charge and discharge under degenerate tariffs, and netted plans still replay clean at identical cost.
6. **No reference schedule curtails solar and none idles with a non-zero magnitude.** Costs are not integers (SAMPLE-01 is 2692.5 kWh), so do not round to whole numbers.
7. **Every window in the pack confirms end-exclusive parsing.** "noon until 2 PM" → `[12,13]`; "6 PM until 9 PM" → `[18,19,20]`; "6 PM until 10 PM" → `[18,19,20,21]`; "between 11 AM and 2 PM" → `[11,12,13]`.
8. **Reserve percentages resolve against capacity**: SAMPLE-03's "50% of capacity" with a 200 kWh battery is 100 kWh, so the interpreter needs `capacity_kwh` in its context.
9. The 10 cases carry 18 interpretations: `solar_reduction` ×3, `minimum_battery_reserve` ×3, `no_charge_window` ×3, `no_discharge_window` ×2, `max_grid_window` ×3, `no_op` ×4 — with 1-, 2-, and 3-note requests and two cases combining two hard directives.

## 4. Contract we implement

### Endpoints

`GET /health` → 200 `{"status":"ok"}`, no LLM call, no external dependency.

`POST /optimize-energy` → 200 with `scenario_id` (echoed unchanged), `directive_interpretation`
(one entry per note, in `note_index` order 0..N−1), `hourly_plan` (exactly 24 entries),
`total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`, `plan_summary`.

Interpretation entry: `note_index`, `applies`, `directive_type`, `structured_adjustment`,
`explanation`. Plan entry: `hour`, `grid_kwh`, `solar_used_kwh`, `battery_action` ∈
{`charge`,`discharge`,`idle`}, `battery_kwh` (non-negative, 0 when idle),
`battery_energy_after_kwh`.

Status codes: 400 for malformed JSON or structurally invalid input (including the mapped
Pydantic errors), 422 unused, 500 only for unanticipated internal faults.

### Directives

| Type | `structured_adjustment` | Compiled effect |
|---|---|---|
| `solar_reduction` | `{"hours":[…],"factor":f}`, `0 ≤ f ≤ 1` | `effective_solar[h] = solar[h] × f` |
| `minimum_battery_reserve` | `{"hours":[…],"minimum_energy_kwh":r}` | `E[h] ≥ max(base_min, r)` |
| `no_charge_window` | `{"hours":[…]}` | `charge[h] = 0` |
| `no_discharge_window` | `{"hours":[…]}` | `discharge[h] = 0` |
| `max_grid_window` | `{"hours":[…],"max_grid_kwh":g}` | `grid[h] ≤ g` |
| `no_op` | `null` | none |

`no_op` is the only type allowed with `applies=false`, and it is the only one with a null
adjustment. Every other type is `applies=true` with the exact shape above. Hours are unique
ascending integers in 0–23. `factor` is the fraction that *remains*: "reduced by 80%" and
"reduced to 20%" both give 0.2.

Combination semantics when several directives touch one hour: union for prohibitions, `max`
for reserve floors, `min` for grid caps. Overlapping `solar_reduction` is undefined in the
statement — we take the lowest remaining factor, isolate it in one function, and note it in
the README as a documented choice.

### Energy rules

```text
E[h] = E[h-1] + charge[h] - discharge[h]
grid[h] + solar_used[h] + discharge[h] = demand[h] + charge[h]
0 <= solar_used[h] <= effective_solar[h]      (surplus is curtailed, no export)
active_min[h] <= E[h] <= capacity
charge[h] <= max_charge, discharge[h] <= max_discharge, one action per hour
E[23] = initial_energy_kwh
minimize sum(grid[h] * tariff[h])
```

## 5. Architecture

```text
request -> strict validation (400 on failure)
        -> one LLM call interpreting all notes  [mandatory LLM step]
        -> canonicalize + deterministic guardrails (retry once, then degrade)
        -> compile directives into 24-element arrays
        -> LP solve, with the re-interpretation ladder on infeasibility
        -> net out any simultaneous charge/discharge, round, recompute totals
        -> independent replay of the serialized numbers
        -> 200 response
```

Suggested modules, one job each:

```text
app/main.py           routes, exception handlers (incl. 422 -> 400)
app/schemas.py        strict request/response/directive models
app/interpreter.py    LLM client, structured output, retry, cache
app/prompts.py        versioned prompt text
app/guardrails.py     canonicalization + validation of model output
app/directives.py     compile validated directives to per-hour arrays
app/optimizer.py      LP build, solve, feasible-subset fallback, net-out
app/replay.py         independent verification of the emitted plan
app/summary.py        deterministic plan_summary
tests/                contract, guardrails, directives, optimizer, replay, samples, paraphrases
harness/run_samples.py  posts all 10 public cases and reports pass/fail
Dockerfile  .dockerignore  .env.example  README.md
```

`plan_summary` is generated deterministically from the validated directives and the solved
strategy. It is scored as a required field, not on wording, so a second LLM call there only
buys latency and failure risk.

### Request validation

Required keys present and correctly typed; 1–3 non-empty note strings; exactly 24 hour
records whose `hour` values are precisely 0..23 with no duplicates; all numbers finite
(reject NaN/Inf); non-negative demand, solar, tariff, capacity, energy, and rates;
`minimum_energy_kwh ≤ initial_energy_kwh ≤ capacity_kwh`. Index hours by their `hour` field
rather than array position, so a reordered but complete array is accepted. Ignore unknown
extra fields (see 1.4).

### LLM interpretation

**Locked: `gemini-3.5-flash-lite` via the Gemini API.** Verified against the current docs —
the model ID is stable (not a `-preview` suffix), and it supports structured outputs and
thinking. Call it over plain REST with `httpx` rather than the `google-genai` SDK: one less
dependency, exact control of timeouts and connection reuse, and trivial to point at a backup
model.

```text
POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent
header: x-goog-api-key: $GEMINI_API_KEY
```

Send the key as a **header, never as `?key=`**, so it cannot leak into an access log, proxy
log, or exception message. That directly protects the secret-safety points.

`generationConfig`: `responseMimeType: "application/json"`, `responseJsonSchema` as below,
`temperature: 0`, `seed` pinned to a constant, `candidateCount: 1`,
`maxOutputTokens: 2048`, and `thinkingConfig: {thinkingLevel: "minimal"}`. That last one
matters for p95 — this is a thinking-capable model, and note interpretation is extraction, not
reasoning, so paying for thought tokens buys latency and nothing else. Use `thinkingLevel`
(the enum) rather than the older integer `thinkingBudget`, which the docs flag as the
pre-Gemini-3 form.

**Structured-output choice:** use one flat JSON Schema object per note and reshape it
deterministically on our side. Current Gemini JSON Schema support can express unions; the flat
shape is nevertheless smaller, easier to validate, and avoids provider-specific union behavior.
It is a deliberate simplicity choice rather than an API limitation:

```json
{"type":"object","properties":{"interpretations":{"type":"array","items":{
  "type":"object","properties":{
    "note_index":{"type":"integer"},
    "directive_type":{"type":"string","enum":["solar_reduction","minimum_battery_reserve",
      "no_charge_window","no_discharge_window","max_grid_window","no_op"]},
    "hours":{"type":["array","null"],"items":{"type":"integer"}},
    "factor":{"type":["number","null"]},
    "minimum_energy_kwh":{"type":["number","null"]},
    "max_grid_kwh":{"type":["number","null"]},
    "explanation":{"type":"string"}},
  "required":["note_index","directive_type","explanation"]}}},
 "required":["interpretations"]}
```

Two consequences worth exploiting. First, the enum constrains `directive_type` at the decoding
level, so an invented directive type is structurally impossible. Second, **we never ask the
model for `applies` or for the nested `structured_adjustment`** — we derive `applies` as
`directive_type != "no_op"` and assemble the exact per-type adjustment ourselves, keeping only
the fields that type allows and dropping the rest. That deletes an entire class of guardrail
failure (wrong `applies`/`no_op` pairing, extra adjustment keys, wrong nesting) instead of
detecting it after the fact. The response schema is prompt-adjacent context, so describe field
meaning in the prompt, not by duplicating the schema in the prompt text.

Backup path, same code and same schema, only the model ID changes: `gemini-3.5-flash`, then
the older `gemini-3.1-flash-lite`. A second API key on a different project is the better hedge
against a quota wall, since a rate limit follows the project, not the model.

Latency budget inside the 30 s ceiling, aiming at p95 ≤ 5 s: a 3 s per-model timeout and an
8 s hard cap on the whole interpretation stage, leaving the LP (milliseconds) and
serialization comfortable room. These values were selected after uncached live burst testing.
Reuse one async HTTP client;
keep the endpoint async so concurrent hidden cases do not queue behind each other.

Prompt must state: the six types and only those; exactly one entry per note in order;
`no_op` semantics; exact per-type shapes; start-inclusive/end-exclusive whole-hour conversion;
AM/PM, noon, midnight, and 24-hour clock handling; **midnight-wrapping windows sorted
ascending**; all-day → 0..23 and never an empty hours array; `factor` as remaining fraction with
a "by 80%" vs "to 20%" contrast; percentage-of-capacity reserves using the supplied capacity;
**the today-scope test for `no_op`** (see 1.8); one directive per note; and a prohibition on
inventing demand, tariffs, battery parameters, or new types.

Cache keyed on hashed normalized notes + capacity + model id + prompt version, bounded in
memory. It speeds up repeats without becoming a lookup table of public cases.

### Guardrails

Canonicalize first (sort and de-duplicate hours and cast integral floats), then validate:
entry count equals note count; `note_index` is exactly
0..N−1; type is allowed; `applies` semantics exact; adjustment has exactly the permitted keys;
hours non-empty and within 0–23; `factor` finite in `[0,1]`; reserve finite, non-negative, and
≤ capacity; grid cap finite and non-negative. On failure, retry once with the concise
validation errors, then degrade per 1.1.

### Optimizer

LP over `grid`, `solar_used`, `charge`, `discharge`, `energy_after` per hour — 120 variables,
48 equality rows, bounds carrying the directives. Equalities: hourly balance and battery
transition. `E[23]` pinned to `initial_energy_kwh`. Solve with HiGHS via
`scipy.optimize.linprog`. Check status explicitly; on infeasibility walk the ladder in 1.3,
which re-interprets before it ever discards, and rewrites discarded notes to `no_op` so the
response stays consistent with itself. Net out simultaneous charge/discharge (required, see
1.2), snap values within ~1e-8 of zero, serialize at 6 decimal places, derive `battery_action`
from the netted values, and recompute all three totals from the serialized numbers.

Optional polish once everything passes: a ~1e-6 penalty on battery throughput to discourage
pointless cycling. On these scenarios that shifts cost by well under 0.01 BDT, inside
tolerance, and yields cleaner plans that sit further from their bounds.

### Replay gate

Written from the request and validated directives, independently of the LP matrices, and run
on the *serialized* numbers because those are what the judge sees: 24 unique ordered hours;
finite non-negative values; `solar_used ≤ effective_solar`; action/magnitude consistency and
`idle ⇒ 0`; hour-by-hour transition, reserve, capacity, and rate limits; prohibition windows;
grid caps; hourly balance; `E[23] = initial`; and the three totals recomputed. The version in
`reference_check.py` already passes all 10 reference plans and can be lifted almost as-is.

If replay fails we have a bug, not a scenario problem. Try the safest feasible rung of the
ladder once; if the result still does not replay, we cannot vouch for the numbers, so log a
sanitized diagnostic and return a 500 rather than emit a schedule we know is wrong (per 1.1).

## 6. Testing

Priority order by scoring value and risk:

1. **Public harness first** — post all 10 cases to the running service, compare interpretation semantics, replay the returned schedule, verify totals, compare cost against the published optimum within 0.01, print per-case pass/fail, exit non-zero on any failure. Never compare plans byte-for-byte; equivalent optima are accepted.
2. **Guardrail unit tests** — duplicate/missing/out-of-order `note_index`, unknown type, wrong adjustment keys, bad `applies`/`no_op` pairings, unsorted/duplicate/fractional/out-of-range hours, NaN/Inf, factor and reserve out of range.
3. **Paraphrase suite, written without looking at the public wording** — 3–5 fresh phrasings per directive; "by" vs "to"; one-fifth/half/percent; 12-hour, 24-hour, noon, midnight; midnight-wrapping windows; all-day; and energy-flavoured `no_op` distractors scoped to yesterday/tomorrow/next month.
4. **Optimizer edges** — flat, rising, falling, and zero tariffs; zero solar and surplus solar; zero capacity and zero rates; initial energy at the reserve and at capacity; binding reserve and grid caps; each infeasibility trigger from 1.3 exercised through the ladder.
5. **Replay property tests** — mutate one field of a good response at a time and confirm replay rejects it; random feasible scenarios to catch indexing and numerical bugs.
6. **Failure and performance** — provider timeout, 429, malformed model JSON, schema-invalid model output; repeated and concurrent valid calls; no secrets in logs or errors; cold start under 60 s; p95 measured against the deployed URL, not localhost.

### Definition of done

All 10 public cases pass interpretation, replay, totals, and optimal cost. No valid request
returns 5xx under injected provider or interpretation failure. No test depends on public
wording or case IDs. p95
under 5 s measured against the deployed endpoint. The hosted URL and a freshly pulled image
both pass the same smoke suite. The README quickstart has been executed literally in a clean
directory. No secrets anywhere in the repo, logs, responses, or image.

## 7. Team split

Three tracks that barely touch each other after the skeleton exists:

- **A — API and LLM**: schemas, validation, exception handlers, interpreter, prompt, guardrails, cache.
- **B — Optimizer and replay**: directive compilation, LP, ladder, net-out, serialization, replay, `plan_summary`, public harness.
- **C — Deployment and docs**: Dockerfile, registry push, hosted deploy, keep-alive, external smoke tests, README, video.

Agree the internal interface (the compiled-directive dataclass) before parallel implementation
so A and B can work against it independently. Solo or two-person team: keep the order but cut the
paraphrase suite to two per directive and skip the property tests.

## 8. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Relevant note marked `no_op` | Case invalid, no optimization credit | Today-scope prompt rule, distractor tests, bias toward applying when torn (1.9). |
| "Reduce by" read as "reduce to" | Wrong factor, wrong schedule | Explicit contrast in the prompt plus dedicated tests. |
| Window off-by-one, noon/midnight, wrap-around | Wrong hours, invalid schedule | End-exclusive rule stated with worked examples; boundary and wrap tests. |
| Model returns valid JSON with invalid semantics | Bad constraints or a crash | Schema-constrained output, canonicalization, guardrails, one retry, then degrade. |
| Interpretation correct but not applied | Up to 25 points plus optimization | Compile only validated directives; replay independently against them. |
| Directives make the LP infeasible | Would have been a 500 | Re-interpretation ladder (1.3); physics never relaxed. |
| Response contradicts its own `directive_interpretation` | Application credit, and obvious to a reviewer | Whatever the optimizer used is what we report; discarded notes become `no_op` (1.3). |
| 5xx after a provider or interpretation failure | Case plus reliability points | The ladder in 1.3 degrades to a valid 200; 500 is reserved for internal inconsistency we cannot vouch for. |
| Provider slow, rate-limited, or down | Latency and failure-rate points | Fast model, pooled async client, tight timeouts, cache, backup models, and a second project key. |
| Platform cold start | Health readiness and p95 | Warm-instance platform, keep-alive ping, measure p95 on the deployed URL. |
| Hosted endpoint dies mid-judging | Deployment points | Same image as the tested Docker fallback; monitor during the window. |
| Solver noise or tiny negatives | Judge replay failure | Snap near-zero values, serialize consistently, replay the emitted numbers. |
| Overfitting to public wording | Hidden paraphrases fail | Meaning-based prompt; paraphrase suite written without the public text. |
| Secrets leak | Security and qualification risk | `.env.example` only, runtime injection, sanitized logs, scan repo and image. |
| README drifts from reality | Reproducibility points | Clean-clone rehearsal using copy-pasted commands. |
| Scope lost to polish | Core score incomplete | Enforce acceptance gates; video only after the scored paths pass. |

## 9. Final checklist

- [ ] `GET /health` externally reachable, returns exactly `{"status":"ok"}`, ready in under 60 s.
- [ ] `POST /optimize-energy` accepts the exact request contract and returns all seven top-level fields.
- [ ] One interpretation entry per note, in `note_index` order, with exact per-type shapes.
- [ ] `no_op` uses `applies=false` + null adjustment; every other directive uses `applies=true`.
- [ ] Directive hours are unique ascending integers 0–23 in every response.
- [ ] The LLM demonstrably produces the interpretation the optimizer consumes, and the README says so.
- [ ] Model output is validated before compilation; invalid output cannot invent a constraint.
- [ ] Every response passes independent replay of the serialized numbers.
- [ ] Totals recomputed from `hourly_plan` and consistent within 0.01.
- [ ] All 10 public cases match expected semantics and optimal cost within tolerance.
- [ ] Provider failure and infeasible directives degrade to a valid 200; 500 only for internal inconsistency.
- [ ] No response contradicts its own `directive_interpretation`; discarded directives are reported as `no_op`.
- [ ] Malformed JSON and invalid structure return 400 (not the FastAPI default 422).
- [ ] p95 ≤ 5 s on the deployed endpoint; no request near 30 s.
- [ ] Image pullable by exact tag/digest, binds `0.0.0.0`, exposes the documented port, reaches `/health`, holds no secrets.
- [ ] README works in a clean clone and covers env var names, model/provider, LLM role, guardrails, solver, run command, curl examples, harness, dependencies, limitations, Docker, and secret handling.
- [ ] Repo created after reveal, private during the event, public after the deadline.
- [ ] Video accessible, under 3:00, explains problem, LLM → guardrails → optimizer flow, and how to run/test.
- [ ] No key, token, `.env`, or stack trace committed, logged, or returned.

## 10. Remaining decisions

### Already locked, no further input needed

| Decision | Choice |
|---|---|
| Interpreter model | `gemini-3.5-flash-lite`, REST via `httpx`, key in `x-goog-api-key` header |
| Structured output | Flat `responseJsonSchema` with a `directive_type` enum; `applies` and `structured_adjustment` derived by us |
| Thinking / sampling | `thinkingLevel: minimal`, `temperature 0`, fixed `seed`, `candidateCount 1` |
| Backup model | `gemini-3.5-flash`, then `gemini-3.1-flash-lite`, same code path |
| Solver | `scipy.optimize.linprog` with HiGHS — pure LP, verified exact on all 10 public cases |
| Stack | Python 3.12-slim, FastAPI, Pydantic v2, uvicorn, async endpoint, one process |
| Validation status codes | 400 for malformed or structurally invalid (explicit `RequestValidationError` handler); 422 unused |
| `plan_summary` | Deterministic template, no second model call |
| Failure policy | Ladder in 1.3: retry, backup model, largest feasible subset with discards reported as `no_op`, then 500 only for internal inconsistency |

### Needs a decision before deployment

**Deployment platform.** Must keep an instance warm — a sleeping free tier breaks the 60-second
readiness check and poisons p95. Fly.io with `min_machines_running = 1` or Cloud Run with
`--min-instances=1` both work; Railway is the least-setup option if there is credit on the
account. Whatever we pick, choose a region near the judges and the Gemini endpoint (Singapore
or Mumbai), because two round trips ride on it.

**Registry and image visibility.** The image must be pullable by judges *while the repository is
still private*. GHCR packages inherit repo visibility by default, so a private repo yields an
unpullable image and forfeits the 4 Docker points unless we explicitly flip the package to
public. Docker Hub with a public repo avoids the trap entirely. Either is fine; the decision is
which, and then actually testing `docker pull` from a logged-out machine.

**How judges run the Docker image without our API key.** This is the sharpest unresolved
question. The image must not contain a key, judges will not have a Gemini key of their own, and
4 of the 10 deployment points ride on the image reaching `/health` with the documented command.
Consequences: `/health` must never touch the LLM, so the container starts and reports healthy
with no key at all — that part is already in the design. What is left to decide is what
`/optimize-energy` does in that container. Options are to document `-e GEMINI_API_KEY=...` and
accept that judges may only exercise `/health` locally while using our hosted URL for the real
calls; or to supply the key through a private submission field if the form has one; or to ship a
clearly-labelled degraded offline mode, which I would avoid because a deterministic interpreter
in the repo invites a question about the mandatory-LLM rule.

**Gemini quota posture.** Free-tier RPM limits are a real risk under repeated hidden cases fired
back to back. Either enable billing on the project, or run two keys on separate projects and
fail over on 429. Free tier with a single key is the one combination I would not ship.

**Team size and split.** Section 7 assumes three tracks. With two people, fold deployment and
docs into whoever finishes first and cut the paraphrase suite to two phrasings per directive.

### Smaller calls, with a default if we say nothing

| Question | Default |
|---|---|
| `operator_notes` outside 1–3 | 400. It contradicts the stated schema, and controlled handling of malformed input is explicitly scored. |
| Retry granularity after a guardrail failure | Identify the invalid/minimal infeasible note set in feedback, then re-request the complete ordered batch so coverage and mapping stay exact. |
| Interpretation cache | On, bounded at ~256 entries, keyed on hashed notes + capacity + model ID + prompt version. |
| uvicorn workers | One. The endpoint is async, the LP takes milliseconds, and extra workers only fragment the cache. |
| Output precision | 6 decimal places, totals recomputed from the serialized values. |
| Throughput tie-break penalty | Off until the harness reports 10/10; it is polish, not correctness. |
| Keep-alive | External pinger (cron-job.org or UptimeRobot) every 5 minutes during the judging window. |
| Overlapping `solar_reduction` | Lowest remaining factor wins, isolated in one function, documented in the README. |

## 11. Decision summary

One schema-constrained LLM call, meaning-preserving canonicalization behind strict
deterministic guardrails, a pure LP that is already proven exact on the public pack, an
independent replay gate, plus reinterpretation and degradation paths that maximize the chance
of a valid 200 for provider, interpretation, and feasibility failures. A sanitized 500 remains
correct when internal correctness cannot be guaranteed. Interpretation and validity carry 50 of the 100 points and
gate the optimization 10, so that is where the night goes. If scope must be cut, protect
interpretation, directive application, replay, the API contract, and deployment before
anything else.
