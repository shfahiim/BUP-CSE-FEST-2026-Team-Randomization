# GridWise Verification Report

Date: 2026-09-18

No API key or secret value is stored in this report, the repository, test artifacts, commands, or container image.

## Result summary

| Test | Result |
|---|---:|
| Offline pytest suite | 17/17 passed |
| Reference model checker | All checks passed |
| Published cases through the real Gemini interpreter | 30/30 passed |
| Published cases through the complete HTTP endpoint | 10/10 passed |
| Unseen hidden-style paraphrases | 50/50 passed |
| Concurrent complete HTTP requests | 10/10 passed |
| Total live LLM-backed evaluated requests | 100/100 passed |
| Docker build and health check | Passed |

Every live pass required all of the following:

- exact machine-checkable directive semantics, ignoring only free-text explanation wording;
- replay against organizer ground-truth directives;
- valid energy balance, solar usage, battery transitions, bounds, rates, and neutrality;
- directive compliance;
- published optimal cost within `0.01` BDT where a public optimum exists.

## Public-case results

The real model correctly interpreted every public note across repeated uncached calls:

- solar reduction and remaining-factor normalization;
- percentage and absolute battery reserves;
- no-charge and no-discharge windows;
- hourly grid caps;
- irrelevant notes mapped to `no_op`;
- multi-note combinations in the correct order.

The final sequential HTTP run passed all 10 cases. Primary-model request times recorded by the service ranged from approximately 1.0 to 1.85 seconds.

## Hidden-style language coverage

The 25-case paraphrase suite was executed twice with caching disabled. It covered:

- “one fifth,” “75% remaining,” and “cut by 30%” solar language;
- percentage-of-capacity reserves;
- `00:00`, noon, midnight, and 12/24-hour expressions;
- windows crossing midnight, returned in ascending order;
- all-day restrictions;
- indirect charge/discharge wording;
- three different grid-cap phrasings;
- past, next-month, next-semester, historical, and advisory energy-themed `no_op` notes;
- a three-note combined scenario.

Both runs passed 25/25. After selecting the final 3-second per-model and 8-second total interpretation deadlines, the live paraphrase run measured:

```text
mean 1.134 s
p95  1.326 s
max  1.666 s
```

## Concurrent endpoint result

Ten unique official cases were submitted to one Uvicorn process with concurrency 5:

```text
failures 0
mean     1.562 s
p95      2.073 s
max      2.073 s
```

All requests used the primary configured model successfully.

## Latency tuning

Earlier uncached burst tests with the original 8-second per-attempt and 20-second total deadline showed occasional 11–16 second outliers, although semantics still passed. The production defaults were tightened to:

```text
LLM_ATTEMPT_TIMEOUT_SECONDS=3
LLM_TOTAL_TIMEOUT_SECONDS=8
```

The final tuned semantic and concurrent runs stayed comfortably below the five-second p95 target. A billed Gemini project and a backup key from a separate project are still recommended because local testing cannot guarantee future provider quota or availability.

## Remaining external checks

The implementation is locally verified. Before submission, still verify:

- the actual hosted public URL from an external network;
- the final registry image by pulling it while logged out;
- the production account's billing/quota configuration;
- a second-project backup key if available;
- continuous reachability during the evaluation window.

Hidden judge wording and inputs are unpublished, so no test can guarantee qualification. The current results provide strong evidence that the interpretation, optimization, schema, replay, latency, and concurrency paths behave as required.
