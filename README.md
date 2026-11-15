# TerraShift AI

**NASA-powered crop-rotation and climate-adaptation intelligence for smallholder and
commercial farms.**

TerraShift AI takes a farm's location, soil profile and current cropping system, pulls
observed climate history from NASA POWER, compares it with recent conditions, and ranks
agronomically plausible crop rotations against five weighted criteria. Every score is
computed by a deterministic engine. Every number in the narrative is checked against that
engine's output. A language model may explain the result; it can never invent or change it.

> Decision-support prototype. Not a substitute for local agronomic advice, soil testing or
> field trials.

---

## What the demo does

| Tab | What it shows |
| --- | --- |
| **01 · Intelligence map** | Farm location, NASA GIBS imagery layers, observed climate KPIs. |
| **02 · Climate stress** | NASA POWER baseline, 90-day anomalies, stress score and evidence-separated signals, forecast panel, vegetation proxy. |
| **03 · Soil & crop** | Farm profile form with explicit measured-vs-missing inputs and a validated crop catalogue. |
| **04 · Rotation simulator** | 3–5 ranked sequences with per-criterion scores, penalties, benefits, concerns and the exact arithmetic. |
| **05 · What-if planner** | Change rainfall, irrigation or farmer priority and see how the ranking moves. |
| **06 · Adaptation report** | Grounded narrative, evidence table, research panel and a print-ready HTML report. |

Four demo farms ship with the app: **Karnataka (India)**, **Iowa (USA)**,
**Machakos (Kenya)** and **Punjab (India)**. They are real coordinates, different climates
and different constraints. Nothing is pre-computed — each card runs the live engine.

---

## Design rules

1. **The engine decides, the model explains.** Criteria scores, ranking and penalties are
   produced by `rotation_engine.py`. The optional Vultr inference layer receives the
   finished JSON and is validated afterwards: any number not present in the computed
   payload causes the model output to be rejected and the deterministic narrative to be
   used instead.
2. **Evidence types stay separate.** Observed (NASA POWER history), forecast (Meteomatics
   or Open-Meteo) and research-backed (crop parameter table, Tavily results) are labelled
   and never blended into a single number.
3. **Missing data is penalised, never invented.** If NDVI statistics are unavailable
   without NASA Earthdata credentials, the API returns `available: false` and the UI says
   so. If NASA POWER is unreachable, the synthetic fallback is flagged `synthetic: true`.
4. **Uncertainty is stated.** Scores are relative rankings, not yield predictions. Water
   accounting explicitly excludes stored soil moisture and groundwater.
5. **No fabricated yields or soil-health percentages.** The product reports indicative
   water balance, nitrogen-fixation credits from the reference table, and qualitative
   agronomic reasoning only.

---

## Quick start (local, no API keys required)

Requires **Python 3.12+**. Node is not needed; the frontend is dependency-free vanilla
JS with Leaflet from a CDN.

```powershell
# from the project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt

# optional: copy and edit credentials
Copy-Item backend\.env.example .env

$env:PYTHONPATH="$PWD\backend"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir "$PWD\backend"
```

Open <http://127.0.0.1:8000>. API docs are at `/docs`, health at `/health`.

macOS/Linux:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
PYTHONPATH=backend python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend
```

SQLite is used by default (`backend/terrashift.db`). No keys are required for NASA POWER,
NASA GIBS or the Open-Meteo forecast fallback.

### Tests

```powershell
$env:PYTHONPATH="$PWD\backend"
python -m pytest backend\tests -q
```

28 tests cover the climate analysis, the rotation engine's scoring and feasibility guards,
the date/input coercion layer, and the HTTP API. They do not call live providers.

---

## Provider matrix

| Capability | Primary source | Fallback | Credentials | Demo state |
| --- | --- | --- | --- | --- |
| Historical climate | **NASA POWER** daily point API | Synthetic, clearly flagged | None | Live |
| Satellite imagery | **NASA GIBS** WMTS tiles | — | None | Live |
| NDVI statistics | NASA Earthdata | Explicit `available: false` + vegetation proxy | `EARTHDATA_*` | Not configured (honest gap) |
| Weather forecast | **Meteomatics** | **Open-Meteo** (keyless), source labelled | `METEOMATICS_*` | Open-Meteo live |
| Agronomy research | **Tavily**, filtered to trusted domains | Suggested authoritative sources, no fabricated results | `TAVILY_API_KEY` | Not configured (honest gap) |
| Narrative | **Vultr Serverless Inference** | Deterministic grounded template | `VULTR_INFERENCE_*` | Template live |
| Storage | SQLite | PostgreSQL on Vultr | `DATABASE_URL` | SQLite |
| Scoring | Local deterministic engine (NumPy/Pandas) | — | None | Always on |

The `/health` endpoint reports which providers are enabled, and every response names the
source that actually answered.

### Environment variables

All variables are optional. Copy `backend/.env.example` to `.env` at the project root (or
`backend/.env`) and fill in what you have.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy URL. Defaults to SQLite; use `postgresql+psycopg://...` on Vultr. |
| `CACHE_DIR`, `CACHE_TTL_SECONDS` | On-disk response cache (default `backend/.cache`, 6 h). |
| `BASELINE_YEARS`, `HTTP_TIMEOUT_SECONDS` | Baseline length and outbound timeout. |
| `METEOMATICS_USERNAME`, `METEOMATICS_PASSWORD` | Enables the Meteomatics forecast. |
| `EARTHDATA_USERNAME`, `EARTHDATA_PASSWORD` | Enables NDVI statistics retrieval. |
| `TAVILY_API_KEY` | Enables the research layer. |
| `VULTR_INFERENCE_API_KEY`, `VULTR_INFERENCE_MODEL` | Enables grounded model narration. |
| `VULTR_INFERENCE_BASE_URL` | Defaults to `https://api.vultrinference.com/v1`. |

Secrets are read from the environment only and are never returned by the API.

---

## Architecture

```text
Browser (vanilla JS + Leaflet, no build step)
        │  fetch JSON
        ▼
FastAPI routes  ──  SQLAlchemy (SQLite / PostgreSQL)
        │
        ├── analysis_service ── climate_analysis (Pandas/NumPy)
        │        ├── nasa_power        observed daily history (live)
        │        └── meteomatics       forecast, Open-Meteo fallback
        │
        ├── rotation_engine    deterministic candidates + weighted scoring + guards
        │        └── crop_knowledge.json   36-entry indicative reference table
        │
        ├── ai_explanations    Vultr inference (optional) + number validation
        │        └── deterministic narrative fallback
        │
        ├── nasa_satellite     GIBS layer templates, honest NDVI status
        ├── tavily             research context only
        ├── cache              file-based TTL cache for provider responses
        └── reports            JSON + print-ready Jinja2 HTML
```

### Rotation scoring

Five criteria, each scored 0–100 and weighted by the selected farmer priority:

| Criterion | What it checks |
| --- | --- |
| Water suitability | Seasonal rainfall + assumed irrigation vs each crop's indicative water need, using the crop's own planting window |
| Climate suitability | Monthly temperature climatology across each crop's growing window, heat exposure and recent rainfall anomaly |
| Soil & nutrient compatibility | Texture, pH and organic-carbon fit; legume nitrogen credit |
| Pest & disease break | Family alternation, crop repetition, previous-crop history |
| Farmer priorities | Alignment with the chosen priority and preferred crops |

Default `balanced` weights: water 0.25, climate 0.25, soil 0.25, pest 0.15, preference 0.10.
The `water`, `soil`, `profit` and `resilience` presets change these weights; the rules and
the data never change.

After weighting, two **agronomic guards** apply a reported penalty and never hide it:

- a crop whose modelled water supply is below **45 %** of indicative need is penalised in
  proportion to the gap;
- continuous monoculture and repeated adjacent crops are penalised.

The API returns `score_before_constraints`, `score_penalty` and `score`, so the arithmetic
is traceable. A crop's water ratio between 45 % and 75 % is reported as a concern rather
than a penalty.

Candidate generation is constrained to agronomically plausible sequences: multi-year forage
legumes are excluded from annual rotations, tropical crops are excluded from cold climates,
and cool-season cereals are not proposed as drought options in warm climates.

### API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Status and enabled providers |
| `GET` | `/api` | Endpoint index |
| `GET` | `/api/config` | Safe runtime configuration |
| `GET` | `/api/crops` | Crop catalogue and provenance |
| `GET` | `/api/demo-farms` | Demo farm presets |
| `POST` | `/api/farms` | Create a farm |
| `GET` | `/api/farms` | List farms |
| `GET` | `/api/farms/{id}` | Read a farm |
| `PATCH` | `/api/farms/{id}` | Update a farm |
| `DELETE` | `/api/farms/{id}` | Delete a farm |
| `POST` | `/api/farms/{id}/analyse` | Climate analysis |
| `GET` | `/api/farms/{id}/weather` | Forecast |
| `GET` | `/api/farms/{id}/satellite` | GIBS layers and NDVI status |
| `POST` | `/api/farms/{id}/rotations` | Ranked rotations |
| `POST` | `/api/farms/{id}/simulate` | What-if scenario |
| `POST` | `/api/rotation/score` | Score one sequence without a farm record (alias: `/api/rotations/score`) |
| `GET` | `/api/farms/{id}/report?format=json` | Full report JSON |
| `GET` | `/api/farms/{id}/report` | Print-ready HTML report |
| `POST` | `/api/cache/clear` | Clear the response cache |

Example what-if request:

```json
{
  "rainfall_change_pct": -20,
  "irrigation": "full",
  "priority": "water",
  "soil_ph": 6.5
}
```

`irrigation` accepts `rainfed`, `limited`, `supplemental` or `full`.

---

## Deploying on Vultr

Vultr is the computational and hosting backbone: the container runs the deterministic
engine and API, PostgreSQL stores farm profiles, and Vultr Serverless Inference provides the
optional grounded narrative.

### 1. Docker + PostgreSQL (recommended)

```bash
# on the Vultr instance
git clone <your-repo> terrashift && cd terrashift
cp backend/.env.example .env
# edit .env: set VULTR_INFERENCE_API_KEY / VULTR_INFERENCE_MODEL, etc.
# set a strong POSTGRES_PASSWORD in the environment or a .env used by compose
docker compose up -d --build
```

`docker-compose.yml` starts PostgreSQL 16 with a health check and the app on port 8000.
The app waits for the database, then serves both the API and the frontend. Data persists in
the `pgdata` volume.

```bash
docker compose logs -f app
curl http://<instance-ip>:8000/health
```

### 2. Docker only, SQLite

```bash
docker build -t terrashift .
docker run -d --name terrashift -p 8000:8000 --env-file .env \
  -v terrashift_data:/app/backend/data terrashift
```

### 3. Direct process

```bash
pip install -r backend/requirements.txt
export DATABASE_URL="postgresql+psycopg://terrashift:password@db-host:5432/terrashift"
export PYTHONPATH=/app/backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir /app/backend
```

### Vultr inference

Set `VULTR_INFERENCE_API_KEY` and `VULTR_INFERENCE_MODEL`. The model is called with a
grounding system prompt and the computed result; the returned text is accepted only if
every number in it appears in the payload. Otherwise the deterministic narrative is served
and the rejection is reported in the response metadata.

### Production notes

- Terminate TLS with a reverse proxy (Caddy, nginx) or Vultr's load balancer.
- `/health` is container-ready for liveness probes.
- Farm data is stored in the configured database; the provider cache is disposable.
- SQLite is fine for the demo; PostgreSQL is recommended for concurrent use.

---

## Project layout

```text
backend/
  app/
    api/            FastAPI routers (farms, analysis, weather, satellite, rotations, reports, meta)
    data/           crop_knowledge.json, demo_farms.json
    models/         SQLAlchemy Farm model
    schemas/        Pydantic request/response models
    services/       NASA POWER, Meteomatics, GIBS, climate analysis, rotation engine,
                    Tavily, grounded explanations, analysis orchestration, cache
    templates/      report.html
    config.py       environment-driven settings
    db.py           engine/session/init
    main.py         app factory, static frontend, /health
  tests/            pytest suite
frontend/           index.html, app.js, styles.css (no build step)
docs/DEMO_SCRIPT.md demo flow and judge-facing notes
Dockerfile
docker-compose.yml
```

---

## Honest limitations

- Scores are **relative rankings for this farm profile**, not calibrated yield or income
  predictions. The weights are explicit prototype values, not universal science.
- The crop table is a 36-entry indicative reference set compiled from published ranges. It
  is not calibrated to local varieties, management or markets.
- Water accounting uses growing-window rainfall and a fixed irrigation assumption per
  cycle. It does **not** model stored soil moisture, groundwater, runoff or irrigation
  efficiency.
- NASA POWER is a coarse gridded product, not an on-farm measurement.
- Without Earthdata credentials, NDVI statistics are unavailable and shown as such; the
  vegetation proxy is labelled a proxy.
- Without Tavily, the research panel lists authoritative starting points and does not
  fabricate citations.
- Fertiliser, pesticide, labour, machinery and market prices are out of scope.

---

## Provenance

- NASA POWER: <https://power.larc.nasa.gov/>
- NASA GIBS: <https://gibs.earthdata.nasa.gov/>
- Meteomatics: <https://www.meteomatics.com/>
- Open-Meteo: <https://open-meteo.com/>
- Tavily: <https://tavily.com/>
- Vultr Serverless Inference: <https://www.vultr.com/products/serverless-inference/>

Built for the NASA Space Apps Challenge 2026 as a same-day, solo, globally demonstrable
prototype.
