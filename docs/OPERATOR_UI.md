# Operator UI: connected dashboard

The operator UI provides the connected dashboard, traffic controls, and a
bounded recovery demonstration for the local Canary Rollback MVP.

## What it shows

- Current stable/canary Gateway traffic weights and acceptance status.
- Stable v1, canary v2, and recovery-controller readiness.
- Prometheus 60-second error percentage and request-rate evidence, including
  the recent 30-minute trend for each release.
- Latest recovery-controller decisions and recovery activity.
- Collapsed demo tools to generate Gateway traffic, inject v2 failures, and
  restore a healthy v2 rollout.

Unavailable dependencies are displayed as unavailable. They are never shown as
healthy simply because a query failed.

## Start it locally

Start the existing local Kubernetes MVP first, including Prometheus and the
recovery controller. The first run needs the Python and frontend dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm --prefix frontend install
```

Then start the full UI from the repository root:

```bash
bash scripts/run_operator_ui.sh
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). The command builds the
React app and serves it from the management API, so there is one process and
one browser address. The Dashboard refreshes cluster data every 10 seconds.

For frontend-only development, `npm --prefix frontend run dev` remains
available at port 5173 and proxies API calls to port 8080.

## Verification page

Open **Verification** to start the existing integrated acceptance runner. The
page shows each scenario as the runner reaches it, streams its latest output,
and blocks all other UI management actions until the suite ends. A normal run
takes about 20–30 minutes and returns the cluster to its healthy 100/0 baseline
through the runner's cleanup process. The page keeps saved `REPORT.md` and
`report.json` artifacts available for download after the run.

## Safety boundary

The browser has no Kubernetes credentials. `management/main.py` runs on the
local operator machine and performs the Kubernetes and Prometheus queries. The
local management API serializes
every route change, traffic job, and failure-rate rollout. It reports actual
command failures, reports unavailable dependencies as unavailable, and prevents
conflicting operations. Verification reports remain available after a completed
run.
