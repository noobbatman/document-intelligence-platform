# GuardianCI Setup Guide

Getting the Document Intelligence Platform and its GuardianCI pipeline running, from a fresh clone to a passing green CI badge.

---

## Prerequisites

| Tool | Minimum version | Notes |
|---|---|---|
| Python | 3.11 | pyenv or system install |
| Docker + Compose | 24 / 2.20 | Docker Desktop covers both |
| Tesseract OCR | 4.1 | `apt install tesseract-ocr` / `brew install tesseract` |
| Git | any | needed by the metrics branch job |

---

## Local development

### 1. Clone and copy env file

```bash
git clone https://github.com/noobbatman/document-intelligence-platform.git
cd document-intelligence-platform
cp .env.example .env
```

### 2. Set your Gemini API key

Open `.env` and fill in:

```
GEMINI_API_KEY=your-key-here
```

The key is required for LLM extraction, query expansion, and the GuardianCI AI review job. Get one at [aistudio.google.com](https://aistudio.google.com).

### 3. Start the stack

```bash
docker compose up -d --build
```

This starts: `api` (port 8000), `worker`, `worker-high`, `worker-webhooks`, `postgres` (5432), `redis` (6379), `minio` (9000 / 9001), `prometheus` (9090), `grafana` (13000).

Wait ~20 s for the database healthcheck, then verify:

```bash
curl http://localhost:8000/api/v1/health/ready
# {"status":"ok","version":"0.3.0","database":"ok","redis":"ok",...}
```

### 4. Run the test suite

```bash
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
pytest --ignore=tests/integration
```

Coverage gate: **80 %** (enforced in CI, not enforced locally unless you pass `--cov-fail-under=80`).

---

## GitHub Actions CI/CD

### Required secrets

Add these under **Settings → Secrets and variables → Actions → Secrets**.

| Secret | Required | What it is |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Gemini key for the AI review and auto-fix jobs |
| `RAILWAY_WEBHOOK_URL` | Yes | Deploy trigger webhook (Railway or any host) |
| `RAILWAY_ROLLBACK_WEBHOOK_URL` | No | Rollback webhook; skipped if absent |
| `SLACK_WEBHOOK_URL` | No | Slack deploy notifications; skipped if absent |

`GITHUB_TOKEN` is injected automatically by GitHub Actions — you do not need to create it.

### Required repository variables

Add these under **Settings → Secrets and variables → Actions → Variables**.

| Variable | Example value | What it is |
|---|---|---|
| `PRODUCTION_HEALTHCHECK_URL` | `https://your-app.up.railway.app/api/v1/health/ready` | URL polled after every deploy to confirm the service is up |
| `GEMINI_MODEL` | `gemma-4-31b-it` (default) | Model used for AI review; override to use a different Gemini model |
| `GUARDIANCI_GEMINI_ENABLED` | `true` (default) | Set to `false` to skip the Gemini review step without removing it |

---

## Pipeline jobs

Every push to `main` (and every PR targeting `main`) runs:

```
test ──┬── docker-smoke ──┬── ai-review ──┐
       │                  │               ▼
       │                  └──────────► deploy   ← push to main only
       │
       └── metrics   ← PR only
       └── auto-fix  ← PR only
```

| Job | Trigger | What it does |
|---|---|---|
| **Tests and coverage** | push + PR | `pytest` with 80 % coverage gate; ruff lint + format check |
| **Docker smoke** | push + PR | Builds the image, starts `api + postgres + redis`, hits `/health/ready` |
| **AI review** | push + PR | Runs `scripts/guardianci_ai_review.py --review-only` via Gemini; writes `guardianci-review-result.json` |
| **Security metrics** | PR only | Publishes score and findings to the `guardianci-metrics` branch |
| **Auto-fix draft PR** | PR only | Runs `--auto-fix-only`; opens a draft PR if Gemini suggests fixes |
| **Deploy** | push to `main` | Triggers deploy webhook, waits up to 600 s (polling every 15 s) for `/health/ready` to return 2xx |

---

## Deploy configuration

The deploy job calls `scripts/guardianci_deploy.py`. It reads these at runtime:

| Env var / arg | Source | Default |
|---|---|---|
| `RAILWAY_WEBHOOK_URL` | secret | — (required) |
| `RAILWAY_ROLLBACK_WEBHOOK_URL` | secret | — (skipped if missing) |
| `SLACK_WEBHOOK_URL` | secret | — (skipped if missing) |
| `PRODUCTION_HEALTHCHECK_URL` | variable | — (health check skipped if empty) |
| `--health-timeout` | hardcoded in workflow | 600 s |
| `--health-interval` | hardcoded in workflow | 15 s |
| `--health-startup-delay` | default in script | 30 s |

The 30-second startup delay prevents spurious failures on hosts (like Render or Railway) that take a moment to swap the container before accepting traffic.

Deployment history is written to an orphan branch named `guardianci-metrics` as `last-deploy.json`. This branch is created automatically on first deploy — no manual setup required.

---

## False-positive suppression

Create `.guardianci/false-positives.json` in the repo root to silence known findings that are intentional or out of scope:

```json
[
  {
    "rule": "hardcoded-secret",
    "path": "tests/fixtures/sample.env",
    "reason": "Test fixture — not a real credential"
  },
  {
    "rule": "sql-injection",
    "path": "scripts/seed_dev_db.py",
    "reason": "Dev-only script; never runs in production"
  }
]
```

Fields: `rule` (finding id), `path` (file glob or exact path), `reason` (free text, shown in the metrics branch). All three fields are required.

---

## Grafana dashboards

Grafana runs at `http://localhost:13000` (Docker Compose) with default credentials `admin / admin`.

Prometheus scrapes the FastAPI app at `http://api:8000/metrics` using the config in `infra/prometheus.yml`. The key metrics exposed:

| Metric | Type | Labels |
|---|---|---|
| `docintel_http_requests_total` | Counter | `method`, `path`, `status` |
| `docintel_http_request_duration_seconds` | Histogram | `method`, `path` |

---

## Common problems

**`RAILWAY_WEBHOOK_URL is required` on deploy**
The secret is missing or named differently. Check Settings → Secrets and confirm the exact name matches.

**Health check times out after deploy**
`PRODUCTION_HEALTHCHECK_URL` is pointing at a URL that isn't reachable from the GitHub Actions runner, or the service isn't fully up within 600 s. Check the Render/Railway dashboard to confirm the deploy completed and the URL is public.

**Tests cancelled after ~25 minutes**
The CI job timeout is 25 minutes (`timeout-minutes: 25` in `ci.yml`). If tests regularly approach this limit, check whether Tesseract or spaCy model downloads are bypassing the uv cache. The cache key is `pyproject.toml` — if that file is unchanged, packages should be cached.

**Coverage below 80 %**
The `pytest` step exits non-zero if coverage drops below `COVERAGE_FAIL_UNDER=80`. Run `pytest --cov=app --cov-report=term-missing` locally to see which lines are uncovered.
