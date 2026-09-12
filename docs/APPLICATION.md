# MVP application: Phase 1

The application is implemented here. Containers, Kubernetes routing, Prometheus
installation, and automated rollback belong to subsequent phases.

## Install and run

Use Python 3.11 or newer, from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Start stable in one terminal:

```bash
APP_VERSION=v1 FAILURE_RATE=0 INSTANCE_ID=stable-local \
  .venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Start a faulty canary in another:

```bash
APP_VERSION=v2 FAILURE_RATE=30 INSTANCE_ID=canary-local \
  .venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8001 --workers 1
```

Open http://127.0.0.1:8000/demo or http://127.0.0.1:8001/demo.
Each currently reaches its own server. A single page will show both versions
once ingress routing is connected. Stop servers with Ctrl+C.

`FAILURE_RATE=30` means a 30 percent independent probability per application
request, not exactly 30 failures per hundred. Use 100 for guaranteed failure
and 0 for a healthy release. These failures do not affect probes or scraping.

Optional `.env` configuration follows `.env.example`. Environment variables
override `.env`; restart after changes. Invalid configuration prevents startup.
`INSTANCE_ID` defaults to the hostname (the pod hostname in Kubernetes).

Run one worker per container because metrics are process-local. Scale using
Kubernetes replicas and scrape each pod separately. Container startup will
bind to `0.0.0.0`; these local examples bind to localhost.

## Endpoints

| Route (GET) | Purpose | Release metrics |
| --- | --- | --- |
| `/` | Version, instance, success or simulated HTTP 500 | Included |
| `/health` | Liveness: process can respond | Excluded |
| `/ready` | Readiness during application lifespan, otherwise 503 | Excluded |
| `/metrics` | Prometheus exposition | Excluded |
| `/demo` | Browser traffic demonstration | Excluded |

There are five routes; automatic API documentation routes are disabled.
A dead or hung process fails liveness by refusing or timing out. Synthetic
application errors leave probes available for the future controller to observe.
Business responses include version and instance, even on unexpected errors.
Exception details stay in server logs. Responses disable caching.

The demo sends sequential requests to `/` approximately twice per second.
It shows HTTP responses by version, 5xx percentage, connection failures, and
the latest 50 requests. Observations are local to that browser session, not
rollout decisions. Use a load generator for sustained canary evaluation.

## Metrics contract

Only `GET /` contributes, including requests with query parameters. Probes,
scrapes, HTML, unsupported methods, and unknown routes are excluded. Demo
fetches to `/` count. Request logs include version, instance, status, duration.

| Metric | Labels |
| --- | --- |
| `requests_total` | `version`, `route`, `method`, `status` |
| `http_errors_total` | `version`, `route` |
| `request_duration_seconds` (histogram) | `version`, `route` |

Known success and error series exist at zero before traffic. Counters reset on
restart. Use Prometheus rates or increases, aggregated across pods, rather than
raw cumulative ratios. Once Prometheus is installed, error percentage is:

```promql
100 * sum(rate(http_errors_total{version="v2",route="/"}[60s]))
  / sum(rate(requests_total{version="v2",route="/",method="GET"}[60s]))
```

Add the application's configured `job` selector to both expressions during
integration. No traffic produces an undefined ratio, not a healthy release.
The controller must enforce observation time, metric availability, and minimum
sample size. Failures before reaching the app (such as ingress errors) are not
measured by application counters.

## Verification

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
curl -i http://127.0.0.1:8000/
curl -i http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/metrics
```

Tests cover configuration, readiness lifecycle, deterministic failure boundaries,
unexpected failures, registry isolation, and exact release metric counts.
