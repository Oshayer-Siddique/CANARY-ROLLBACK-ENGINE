# Prometheus monitoring guide

Related: [Phase 4 errors, investigation, corrections, and remaining limitations](troubleshooting/PHASE_4_MONITORING.md).

The [Phase 6 recovery controller](AUTOMATED_RECOVERY.md) now consumes these health signals. Keep isolated Phase 4 failure tests at 100/0; active unhealthy canary traffic can now trigger automatic restoration.

Phase 5 is now implemented: see [CANARY_TRAFFIC.md](CANARY_TRAFFIC.md) for shared Gateway traffic and its verification. This Phase 4 guide assumes the 100/0 baseline; restore it with `python3 scripts/set_canary_traffic.py --canary-percent 0` before the failure exercise. Do not run failure injection concurrently with Phase 5 healthy-traffic verification.

This document describes Phase 4 of the Canary Rollback Engine MVP. In this phase we turn the Kubernetes application from something that merely runs into something we can measure: Prometheus collects request and error metrics from both releases, turns those raw counters into a 60-second health signal, and raises an alert when the canary is unhealthy.

Phase 4 is the **observation layer**, not the decision or recovery layer. The Gateway still sends external traffic only to stable v1. We deliberately send direct, controlled requests to the isolated canary Service so we can prove that monitoring identifies a healthy and an unhealthy v2 without exposing normal users to it. Phase 5 will introduce controlled Gateway traffic splitting; Phase 6 will consume the health signal and remove a failed canary from traffic automatically.

## What we are doing in the MVP lifecycle

```text
Phase 3 — Kubernetes foundation       COMPLETE
  stable v1 + canary v2 + isolated Services + Gateway (100% v1)
                                      |
                                      v
Phase 4 — Monitoring and health       COMPLETE (validated locally)
  scrape both releases -> calculate 60-second error percentage -> raise alert
                                      |
                                      v
Phase 5 — Canary traffic              NEXT
  change Gateway from 100/0 to a controlled stable/canary split
                                      |
                                      v
Phase 6 — Automated recovery          LATER
  read the health decision -> remove failed v2 -> keep users on v1
```

The important boundary is intentional: a firing `CanaryErrorRateHigh` alert in this phase proves detection only. It does **not** alter a Deployment, Service, HTTPRoute, or Gateway weight. That separation lets us validate the health rule safely before we grant any component the authority to change production traffic.

## Validated completion

The complete local verifier has passed against `k3d-canary-mvp`: Prometheus found all four application Pods, measured healthy v1 and v2 traffic at zero percent errors, detected a deliberately failing v2 above the 5 percent policy threshold, fired `CanaryErrorRateHigh`, then observed restored v2 traffic at zero percent and confirmed that the alert cleared. The verifier restores `FAILURE_RATE=0` before it exits, so the cluster finishes in its healthy baseline state.

## What this phase provides

```text
application-v1 pods (3) ─┐
                         ├─ /metrics every 5 seconds ─> Prometheus
application-v2 pod (1) ──┘                                  |
                                                             v
                                                60-second release-health rules
                                                             |
                                        request rate, error ratio, error percentage, alert
```

Prometheus runs in its own `monitoring` namespace and stores data on a one-gigabyte local persistent volume. It discovers only Pods in the `canary-mvp` namespace that have the application label and the named `http` port.

## Repository files

| Path | Purpose |
| --- | --- |
| `infra/kubernetes/monitoring/namespace.yaml` | Creates the `monitoring` namespace. |
| `infra/kubernetes/monitoring/rbac.yaml` | Grants Prometheus read-only Pod discovery in `canary-mvp`. |
| `infra/kubernetes/monitoring/storage.yaml` | Creates the 1 GiB Prometheus data claim. |
| `infra/kubernetes/monitoring/prometheus.yaml` | Prometheus configuration, health rules, Deployment, and Service. |
| `infra/kubernetes/monitoring/kustomization.yaml` | Applies all monitoring resources together. |
| `scripts/deploy_monitoring.sh` | Installs or updates the monitoring stack. |
| `scripts/port_forward_prometheus.sh` | Opens the Prometheus UI on the local machine. |
| `scripts/generate_traffic.py` | Sends controlled business requests to stable or canary. |
| `scripts/verify_monitoring.py` | Verifies discovery, metrics, and optionally failing-canary recovery. |

## Prerequisites

Phase 3 must already be running. Confirm this first:

```bash
kubectl --context k3d-canary-mvp -n canary-mvp get deployments,pods,services
```

Expected result: three ready stable Pods, one ready canary Pod, and the stable/canary Services. The single local canary replica is pinned to the k3d server node so the built `application:v2` image is always available during repeated failure/recovery rollouts; this is a local-MVP constraint, not a production scheduling pattern. Docker, `kubectl`, Python 3, and the local k3d cluster are also required.

## Install or update monitoring

From the repository root, run:

```bash
./scripts/deploy_monitoring.sh
```

The script checks the k3d context and application workloads, applies the monitoring manifests, restarts Prometheus to load any ConfigMap rule changes, waits for the rollout, and prints the Deployment, Pod, Service, and persistent-volume status.

The Prometheus Deployment uses `Recreate` strategy. It has one replica and a `ReadWriteOnce` volume, so this prevents a rolling update from attempting to mount the same local volume on old and new Pods at the same time.

Check the result:

```bash
kubectl --context k3d-canary-mvp -n monitoring get deployment,pods,service,pvc
```

The PVC should be `Bound` and the Prometheus Pod should be `1/1 Running`.

## Open Prometheus

Run this command in one terminal and keep it open:

```bash
./scripts/port_forward_prometheus.sh 19090
```

Then open [http://127.0.0.1:19090](http://127.0.0.1:19090) in a browser. Stop the port-forward with `Ctrl+C` when finished.

The Prometheus Targets page is available at `/targets`. After a few seconds, it should show four healthy `canary-application-pods` targets: three v1 Pods and one v2 Pod.

## How scraping works

The application already exposes `/metrics`. Prometheus discovers Pods through the Kubernetes API and keeps only targets that meet all of these conditions:

- Namespace is `canary-mvp`.
- Pod label `app.kubernetes.io/name` is `canary-demo-application`.
- Container port name is `http`.
- Pod phase is `Running`.

The ServiceAccount has only `get`, `list`, and `watch` permission on Pods in `canary-mvp`. It has no write permission and cannot discover arbitrary cluster workloads.

Prometheus scrapes every five seconds. This short local interval gives the 60-second health rule enough samples quickly while remaining small enough for the MVP cluster.

Each target carries these useful labels:

| Label | Meaning |
| --- | --- |
| `pod` | Kubernetes Pod name. |
| `release` | `stable` or `canary`. |
| `app_version` | Version copied from the Pod label. |
| `version` | Application metric label: `v1` or `v2`. |

`honor_labels: true` preserves the application metric's `version` label, so release calculations remain directly tied to the version returned by the application.

## Health signals

The application emits these counters for business requests to `GET /`:

| Metric | Meaning |
| --- | --- |
| `requests_total` | Completed requests, labelled by version, status, route, and method. |
| `http_errors_total` | Completed HTTP 5xx requests, labelled by version and route. |
| `request_duration_seconds` | Request-duration histogram. |

Prometheus records three reusable 60-second signals:

```promql
canary:request_rate_60s
canary:error_rate_ratio_60s
canary:error_percentage_60s
```

The error percentage is:

```text
100 × rate(http_errors_total over 60 seconds)
      ÷ rate(requests_total over 60 seconds)
```

The calculation sums all replicas before dividing. This matters because stable v1 has three Pods while v2 has one. It therefore measures the release as a whole rather than treating a single Pod as the release.

The `CanaryErrorRateHigh` alert fires when v2 has more than 5% errors over the rolling 60-second window and at least three requests in that window. The three-request minimum avoids describing no traffic as a healthy or unhealthy canary.

The alert is visible in Prometheus under `/alerts` and through this query:

```promql
ALERTS{alertname="CanaryErrorRateHigh",alertstate="firing"}
```

No Alertmanager is installed in Phase 4 because there is no external notification destination yet. Phase 6 will query these Prometheus signals and decide whether to remove canary traffic.

## Useful PromQL queries

Paste these into the Prometheus query page.

```promql
# All application targets currently scrapeable
up{job="canary-application-pods"}

# Number of healthy application targets; expected value is 4
count(up{job="canary-application-pods"} == 1)

# Request rate by application version in the last 60 seconds
canary:request_rate_60s

# Error percentage by application version in the last 60 seconds
canary:error_percentage_60s

# Direct error percentage calculation for v2
100 * sum(rate(http_errors_total{job="canary-application-pods",version="v2"}[60s]))
  / clamp_min(sum(rate(requests_total{job="canary-application-pods",version="v2",route="/"}[60s])), 0.000001)

# Request duration at the 95th percentile by version
histogram_quantile(0.95, sum by (le, version) (rate(request_duration_seconds_bucket{job="canary-application-pods"}[60s])))
```

## Generate safe test traffic

Gateway traffic remains 100% stable in Phase 4, so use the isolated Services to create deterministic traffic for each version:

```bash
./scripts/generate_traffic.py --target stable --requests 30
./scripts/generate_traffic.py --target canary --requests 30
```

The script creates a temporary local port-forward, sends concurrent requests to `/`, prints a JSON summary, and closes the port-forward. It does not change route weights, Deployments, or namespaces.

A healthy result looks like:

```json
{"target":"canary","requests":30,"statuses":{"200":30},"versions":["v2"]}
```

After one or two scrape intervals, query `canary:error_percentage_60s`. Both versions should show zero after healthy traffic.

## Automated verification

Run the normal Phase 4 smoke test:

```bash
python3 scripts/verify_monitoring.py
```

It verifies that Prometheus discovers all four Pods, captures generated stable/canary requests, and reports zero-percent errors while both releases are healthy.

Run the complete failing-canary exercise:

```bash
python3 scripts/verify_monitoring.py --exercise-canary
```

That exercise temporarily sets `FAILURE_RATE=100` on v2, waits until Prometheus is scraping the replacement v2 Pod, then waits for its rolling error signal to exceed the real 5-percent policy threshold and for the `CanaryErrorRateHigh` alert, restores `FAILURE_RATE=0`, waits for Prometheus to scrape the restored v2 Pod, then sends healthy v2 traffic, and waits for the old bad samples to leave the 60-second window. The script restores `FAILURE_RATE=0` again in cleanup if an intermediate check fails.

The complete exercise usually takes four to six minutes: v2 is rolled out once to inject failure, once to recover, and the rule intentionally waits for a real 60-second window to age out failed samples.

## Manual failing-canary test

If you want to inspect the process manually:

```bash
kubectl --context k3d-canary-mvp -n canary-mvp \
  set env deployment/application-v2 FAILURE_RATE=100
kubectl --context k3d-canary-mvp -n canary-mvp \
  rollout status deployment/application-v2 --timeout=180s
./scripts/generate_traffic.py --target canary --requests 30
```

After Prometheus scrapes the traffic, v2 should exceed 5% and the alert should fire. Restore normal behavior afterward:

```bash
kubectl --context k3d-canary-mvp -n canary-mvp \
  set env deployment/application-v2 FAILURE_RATE=0
kubectl --context k3d-canary-mvp -n canary-mvp \
  rollout status deployment/application-v2 --timeout=180s
```

The error signal may remain high for up to 60 seconds after restoration because it is correctly measuring the trailing 60-second window.

## Operations and troubleshooting

```bash
# Prometheus logs
kubectl --context k3d-canary-mvp -n monitoring logs deployment/prometheus --tail=100

# Validate Prometheus configuration and loaded rules
kubectl --context k3d-canary-mvp -n monitoring exec deployment/prometheus -- \
  promtool check config /etc/prometheus/prometheus.yml

# Inspect the discovery permission
kubectl --context k3d-canary-mvp auth can-i list pods \
  --as=system:serviceaccount:monitoring:prometheus -n canary-mvp

# Reapply configuration after changing a manifest
./scripts/deploy_monitoring.sh
```

| Symptom | Check |
| --- | --- |
| Prometheus Pod is pending | Inspect the PVC and local-path provisioner: `kubectl -n monitoring get pvc,pods`. |
| Pod restarts after a config edit | View `kubectl -n monitoring logs deployment/prometheus`; invalid PromQL or YAML prevents startup. |
| Fewer than four targets | Confirm all application Pods are Running and their labels/port name match the discovery filters. |
| Target is down | Check `/metrics` directly through the matching Service and inspect Pod events. |
| No canary metrics | Generate direct canary traffic; the Phase 4 Gateway route still sends external traffic only to stable. |
| Alert did not fire | Send at least three v2 requests, wait for a scrape/rule evaluation, and confirm `FAILURE_RATE` is nonzero. |
| Error signal persists after restoring v2 | Wait until the previous bad samples are older than 60 seconds. |
| Browser connection refused | Start `scripts/port_forward_prometheus.sh 19090` and use that exact local port. |

## Cleanup

To remove only monitoring resources and keep the application cluster intact:

```bash
kubectl --context k3d-canary-mvp delete -k infra/kubernetes/monitoring
```

This removes the Prometheus namespace and its persistent volume claim. The application namespace, Gateway, and local Docker images remain. Re-run `./scripts/deploy_monitoring.sh` to recreate monitoring.

## Phase 4 completion criteria

Phase 4 is complete when:

- Prometheus is running with persistent local storage.
- It discovers three stable Pods and one canary Pod automatically.
- It scrapes `/metrics` and preserves per-version data.
- It calculates the request rate and error percentage over 60 seconds.
- It shows a v2 alert above the 5% error threshold when there is sufficient traffic.
- Healthy traffic reports zero-percent error signals.
- A failing v2 can be restored and its alert clears after the rolling window passes.
- Deployment, access, queries, verification, troubleshooting, and cleanup are documented.

Phase 5 now provides verified 90/10 Gateway routing and manual restoration; see [CANARY_TRAFFIC.md](CANARY_TRAFFIC.md). Phase 6 will use these monitoring signals to decide automatic recovery. Phase 7 will verify the complete integrated workflow.
