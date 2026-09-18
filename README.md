# GridWise Energy Optimizer

GridWise is a deployable HTTP service for the BUP CSE Fest 2026 preliminary challenge. It interprets natural-language operator notes with Gemini, validates the resulting directives deterministically, solves the 24-hour energy schedule as a linear program, and independently replays the serialized response before returning it.

## Architecture

```text
POST /optimize-energy
  -> Pydantic request validation
  -> Gemini structured interpretation
  -> deterministic directive guardrails
  -> per-hour constraint compilation
  -> SciPy/HiGHS linear optimization
  -> mandatory charge/discharge netting
  -> independent replay and totals verification
  -> JSON response
```

The LLM participates directly in the path that creates optimizer constraints. It does not calculate the schedule. Energy balance, battery behavior, directive application, optimization, and response verification are deterministic.

## Requirements

- Python 3.12
- A Gemini API key with quota for the configured model
- Docker only if using the container workflow

## Hosted API

The evaluation service is publicly reachable at:

```text
http://161.118.236.136:3001
```

Verify readiness:

```bash
curl --fail http://161.118.236.136:3001/health
```

Expected response:

```json
{"status":"ok"}
```

Run all supplied public cases against the hosted service:

```bash
python harness/run_samples.py --base-url http://161.118.236.136:3001
```

The hosted service runs directly under Python/Uvicorn as a restart-enabled `systemd` service with one worker. Port 3001 is the public deployment; the Docker image documented below is the required fallback artifact.

## Configuration

Copy `.env.example` or set these environment variables through the deployment platform:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes for real interpretation | unset | Primary Gemini API key |
| `GEMINI_BACKUP_API_KEY` | Recommended | unset | Key from a separate project for quota failover |
| `GEMINI_MODELS` | No | `gemini-3.5-flash-lite,gemini-3.5-flash,gemini-3.1-flash-lite` | Ordered model attempts |
| `GEMINI_BASE_URL` | No | Google Gemini v1beta URL | API base URL, useful for testing |
| `LLM_ATTEMPT_TIMEOUT_SECONDS` | No | `3` | Per-model request timeout |
| `LLM_TOTAL_TIMEOUT_SECONDS` | No | `8` | Total interpretation deadline |
| `INTERPRETATION_CACHE_SIZE` | No | `256` | Bounded in-memory cache entries |
| `OUTPUT_DECIMAL_PLACES` | No | `6` | Response numeric precision |
| `PORT` | No | `8080` in Docker | Listening port |

Never commit `.env` or place a key in the Docker image. The service sends the Gemini key in the `x-goog-api-key` header rather than the URL.

## Local quickstart

```bash
git clone https://github.com/shfahiim/gridwise-bup-cse-fest-2026.git
cd gridwise-bup-cse-fest-2026
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
# Open .env and set GEMINI_API_KEY. Never commit this file.
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

The application loads `.env` automatically for local development. Variables supplied by the shell or deployment platform take precedence, so production credentials do not need a file.

In another terminal:

```bash
curl -s http://127.0.0.1:8080/health
```

Expected response:

```json
{"status":"ok"}
```

Run one supplied sample:

```bash
jq '.cases[0].input' BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json \
  | curl -sS -X POST http://127.0.0.1:8080/optimize-energy \
      -H 'Content-Type: application/json' --data-binary @-
```

Run the complete public HTTP harness:

```bash
python harness/run_samples.py --base-url http://127.0.0.1:8080
```

The harness checks interpretation semantics, independently replays each schedule, verifies totals, and compares cost with the published optimum while allowing equivalent hourly plans.

Optional live-model and concurrent checks:

```bash
python harness/live_model_check.py --repetitions 1
python harness/live_paraphrase_check.py
python harness/load_check.py --base-url http://127.0.0.1:8080 --concurrency 5
```

## Tests

The automated suite does not need an API key. It injects the public ground-truth interpretations to test the deterministic optimizer independently from provider availability.

```bash
python -m pytest -q
python reference_check.py
```

The tests cover all public cases, request status behavior, semantic guardrails, non-finite values, provider-outage degradation, infeasible-directive recovery, LP degeneracy, mandatory netting, replay, totals, and optimal cost.

## Docker

Build and run locally:

```bash
docker build -t gridwise:1.0.0 .
docker run --rm -p 8080:8080 \
  --env-file .env \
  gridwise:1.0.0
```

Verify the container:

```bash
curl -s http://127.0.0.1:8080/health
python harness/run_samples.py --base-url http://127.0.0.1:8080
```

The image runs as a non-root user, binds to `0.0.0.0`, exposes port 8080, and contains no credentials. `/health` intentionally does not call Gemini, so the fallback image can demonstrate readiness without a key. `/optimize-energy` requires a runtime key for scored language interpretation.

## VM service operation

The checked-in `deploy/gridwise.service` unit starts the direct VM deployment. On the submitted VM it uses:

- application directory: `/home/ubuntu/gridwise`
- environment file: `/home/ubuntu/gridwise/.env` with mode `600`
- Python environment: `/home/ubuntu/gridwise/.venv`
- process: one Uvicorn worker bound to `0.0.0.0:3001`

Operational checks:

```bash
sudo systemctl status gridwise.service
sudo journalctl -u gridwise.service -n 50 --no-pager
sudo systemctl restart gridwise.service
```

### Container image release

The repository's Docker workflow publishes version tags unchanged. The submission image is:

```text
ghcr.io/shfahiim/gridwise-bup-cse-fest-2026:v1.0.0
```

Once its package visibility is public, organizers can run it with:

```bash
docker pull ghcr.io/shfahiim/gridwise-bup-cse-fest-2026:v1.0.0
docker run --rm -p 8080:8080 --env-file .env \
  ghcr.io/shfahiim/gridwise-bup-cse-fest-2026:v1.0.0
curl -s http://127.0.0.1:8080/health
```

The source repository is intentionally private during development. GitHub Container Registry package visibility is managed separately from repository visibility: before evaluation, make both the repository and the package public, then confirm the image can be pulled from a logged-out shell. For the final submission, prefer an immutable release tag or digest instead of relying only on `latest`.

## API contract

### `GET /health`

Returns HTTP 200:

```json
{"status":"ok"}
```

### `POST /optimize-energy`

Accepts the exact scenario shape from the problem statement and returns:

- `scenario_id`
- one ordered `directive_interpretation` entry per operator note
- exactly 24 `hourly_plan` entries
- `total_grid_kwh`
- `total_cost_bdt`
- `peak_grid_kwh`
- `plan_summary`

Malformed JSON and structurally invalid requests return HTTP 400. Provider or interpretation failures degrade to a physically valid base schedule when possible. A sanitized HTTP 500 is reserved for internal failures where the service cannot guarantee correctness.

## Interpretation and recovery behavior

The normal path makes one structured Gemini request for all notes. Output is treated as untrusted and checked for note coverage, directive type, hour normalization, applies semantics, adjustment shape, finite numeric values, solar factors, battery reserve bounds, and non-negative grid caps.

If a valid interpretation makes the LP infeasible:

1. find a minimal individually or jointly infeasible note set;
2. ask the model to reinterpret with feasibility feedback;
3. try configured backup models/keys;
4. preserve the largest feasible directive subset;
5. rewrite discarded notes to `no_op` so the response never contradicts the schedule.

If every provider attempt fails before producing an interpretation, the service returns one `no_op` per note and attempts a verified base schedule. This is an emergency reliability path, not a replacement for the mandatory LLM interpretation path.

## Optimization model

For every hour, the LP models grid import, solar used, battery charge, battery discharge, and ending battery energy. It enforces energy balance, solar limits, battery bounds and transitions, charge/discharge rates, all supported directives, and end-of-day neutrality. The objective minimizes total grid electricity cost.

An LP can contain simultaneous charge and discharge in a degenerate optimum. The service therefore nets them after solving. Netting preserves the battery trajectory, energy balance, cost, and all rate limits while producing the single action required by the response schema. The final rounded response is then replayed independently.

## Known limitations and assumptions

- Provider latency, quota, and availability remain external dependencies. Use a billed project and a backup key for evaluation.
- Interpretation caching is process-local; one Uvicorn worker is recommended.
- The statement does not define overlapping solar-reduction semantics. This implementation applies the lowest remaining factor in an affected hour and isolates that policy in `app/directives.py`.
- Battery efficiency is treated as 100% because the official equations specify direct charge/discharge state transitions without an efficiency parameter.
- The fallback can preserve API and physics validity during provider failure, but it cannot recover hidden interpretation points for a discarded true directive.

## Dependencies and credits

- Google Gemini API — language-model interpretation of operator notes
- FastAPI, Pydantic, Uvicorn, and HTTPX — HTTP service, schemas, server, and provider client
- SciPy/HiGHS and NumPy — deterministic linear optimization and numeric handling
- python-dotenv — local configuration loading
- pytest — offline acceptance tests
- Docker and GitHub Container Registry — reproducible fallback packaging

Exact versions are pinned in `requirements.txt` and `requirements-dev.txt`. The application architecture, prompts, guardrails, directive compiler, optimizer integration, replay gate, test harnesses, and recovery behavior are implemented in this repository.

## Project layout

```text
app/
  main.py          HTTP routes and sanitized errors
  schemas.py       request, directive, plan, and response models
  interpreter.py   Gemini REST client, failover, deadlines, cache
  guardrails.py    canonicalization and semantic validation
  directives.py    per-hour directive compilation
  optimizer.py     HiGHS LP, netting, feasibility diagnosis
  replay.py        independent serialized-response verification
  service.py       end-to-end orchestration and recovery
tests/              offline deterministic acceptance tests
harness/            deployed public-case HTTP harness
deploy/             production systemd service unit
```
