# Operator UI: connected dashboard

The operator UI is the first frontend phase for the local Canary Rollback MVP.
It is deliberately read-only at this stage: it shows the actual cluster state
without allowing a browser to access Kubernetes or Prometheus directly.

## What it shows

- Current stable/canary Gateway traffic weights and acceptance status.
- Stable v1, canary v2, and recovery-controller readiness.
- Prometheus 60-second error percentage and request-rate evidence, including
  the recent 30-minute trend for each release.
- Latest recovery-controller decisions and recovery activity.

Unavailable dependencies are displayed as unavailable. They are never shown as
healthy simply because a query failed.

## Start it locally

Start the existing local Kubernetes MVP first, including Prometheus and the
recovery controller. From the repository root, use two terminals:

```bash
# Terminal 1: management API
python3 -m pip install -e '.[dev]'
python3 -m uvicorn management.main:app --host 127.0.0.1 --port 8080
```

```bash
# Terminal 2: React dashboard
cd frontend
npm install
npm run dev
```

Open the address printed by Vite, normally `http://127.0.0.1:5173`.
The dashboard refreshes live data every 10 seconds. Its Vite development proxy
forwards `/api` requests to the local management API on port 8080.

## Safety boundary

The UI has no Kubernetes credentials. `management/main.py` runs on the local
operator machine and performs read-only `kubectl` and Prometheus proxy queries.
Traffic changes, failure injection, and full verification will be introduced
only after the management API provides tracked operations and blocks conflicting
workflows.
