# Phase 5: Canary traffic control and verification

Related: [Phase 5 issue history and safeguards](troubleshooting/PHASE_5_CANARY_TRAFFIC.md).

Phase 6 now connects these traffic controls to an in-cluster recovery controller. See [AUTOMATED_RECOVERY.md](AUTOMATED_RECOVERY.md) before running failure injection with canary traffic active.

## 1. What we are doing

Phase 5 connects our existing stable and canary releases to shared Gateway traffic. An operator can introduce v2 at 10 percent, observe responses and Prometheus metrics, and manually restore all new traffic to v1.

Phase 3 provided the workloads, isolated Services, and NGINX Gateway. Phase 4 added Prometheus discovery and 60-second health signals. Phase 5 uses those components without another ingress controller or rollout framework. Phase 6 will automate the health-to-routing decision. Phase 7 will verify the complete release and recovery lifecycle.

Phase 5 is implemented and its live verification passed on September 13, 2026. It provides traffic control and observation. It does not automatically promote or roll back a release: a firing Prometheus alert does not change route weights on its own.

## 2. Architecture

```text
Client / traffic generator
          |
canary-gateway-nginx Service (NGINX data plane)
          |
canary-gateway -> application-route HTTPRoute
          |
          +-- weight 90 -> application-stable -> 3 v1 pods
          |
          +-- weight 10 -> application-canary -> 1 v2 pod

Prometheus independently scrapes /metrics on all four application pods.
```

The application resources use context `k3d-canary-mvp` and namespace `canary-mvp`; Prometheus is in namespace `monitoring`.

| State | Stable weight | Canary weight | Purpose |
| --- | ---: | ---: | --- |
| Baseline | 100 | 0 | Starting configuration |
| Canary active | 90 | 10 | Evaluate v2 with limited traffic |
| Manual restoration | 100 | 0 | Return new traffic to v1 |

Weights apply between Services. Three stable pods and one canary pod do not imply a 75/25 split. Direct requests to either application Service bypass Gateway routing. Phase 5 leaves the existing local v2 node pin and replica counts intact.

The checked-in HTTPRoute defines both backends at **100/0**. Reapplying it, including through `deploy_kubernetes.sh`, resets an active canary to baseline. Activating 90/10 is an explicit operational command.

## 3. Repository files

| File | Responsibility |
| --- | --- |
| `infra/kubernetes/gateway/httproute.yaml` | Stable-only baseline with both backend references |
| `scripts/set_canary_traffic.py` | Validate prerequisites, update weights atomically, verify controller acceptance |
| `scripts/generate_traffic.py` | Direct/Gateway traffic, pacing, version counts, HTTP statuses, and errors |
| `scripts/verify_canary_traffic.py` | Baseline, distribution, monitoring, and restoration exercise |
| `tests/test_canary_traffic.py` | Offline routing-safety and response-accounting checks |

Operational Python scripts use the standard library. Run the following commands from the repository root; no additional load-testing framework is required.

## 4. Prerequisites and inspection

Install the local cluster, application images, NGINX Gateway, and Prometheus using the earlier [Kubernetes](KUBERNETES.md) and [monitoring](MONITORING.md) guides. Inspect them with:

```bash
kubectl --context k3d-canary-mvp -n canary-mvp get deployments,services,gateway,httproute
kubectl --context k3d-canary-mvp -n monitoring get deployment,pods,pvc
kubectl --context k3d-canary-mvp -n canary-mvp get httproute application-route \
  -o jsonpath='{.spec.rules[0].backendRefs}'
```

Full verification requires three ready v1 replicas, one ready v2 replica, and `FAILURE_RATE=0` explicitly set in both Deployment templates. The verifier checks these conditions and does not change failure settings. Avoid running the Phase 4 failure exercise, competing traffic generators, or another route writer during the test.

## 5. Activate and restore traffic

```bash
# Activate 90 percent stable / 10 percent canary.
python3 scripts/set_canary_traffic.py --canary-percent 10

# Restore 100 percent stable / 0 percent canary.
python3 scripts/set_canary_traffic.py --canary-percent 0
```

The command prints JSON containing stable/canary percentages, route generation, and controller acceptance. Other integer percentages from 0 through 100 are supported for manual experiments; the complete Phase 5 verifier always exercises 10. Invalid input is rejected before Kubernetes writes.

Before changing traffic, the command checks the expected single-rule route, Gateway readiness, both Service definitions and port 80, and ready non-terminating endpoints for each backend receiving positive weight. An unready canary does not block restoration to stable as long as its Service still exists and stable has a ready endpoint.

Both weights are updated in one JSON patch with a resource-version precondition. Concurrent modification is rejected rather than overwritten silently. The command waits for `Accepted` and `ResolvedRefs` on the intended Gateway parent at the **current route generation**. Old success conditions do not count.

Controller acceptance confirms configuration processing. Actual traffic is checked separately by the verifier. A command timeout may occur after the patch applied; inspect current weights and restore baseline if necessary. There is no hidden automatic undo in the standalone traffic command.

Weight changes affect subsequent routing decisions. Existing requests may complete on the old backend. Verification allows observed convergence before collecting a fresh measured sample.

## 6. Generate Gateway traffic

```bash
# Burst sample for distribution measurement.
python3 scripts/generate_traffic.py --target gateway --requests 2000

# Approximately 80 seconds of traffic for rolling monitoring signals.
python3 scripts/generate_traffic.py --target gateway --requests 800 --interval 0.1

# Earlier direct Service tests remain available.
python3 scripts/generate_traffic.py --target stable --requests 30
python3 scripts/generate_traffic.py --target canary --requests 30
```

`--interval` specifies seconds between scheduled request starts; default is zero for a burst. Workers default to five. Slow requests can reduce actual throughput, so this is a controlled test generator, not a guaranteed requests-per-second benchmark.

Each command opens a temporary localhost port-forward, waits for readiness, requests `/`, prints JSON, and closes its connection. Gateway mode forwards the NGINX data-plane Service: HTTPRoute routing still occurs. Direct Service port-forwarding selects an application pod and does not measure distribution among every replica.

The report contains attempted requests, completed HTTP responses, version counts, version percentages, statuses per version, overall statuses, transport errors, unexpected response bodies, and limited error examples. Percentages use **all attempted requests** as the denominator; failures are not silently excluded.

Valid application HTTP 500 responses are counted without making the generator fail, preserving Phase 4 failure-testing behavior. Transport errors and unexpected response bodies produce a nonzero generator exit. The healthy Phase 5 verifier additionally requires every status to be 200.

A 90/10 configuration need not produce exactly 1,800 v1 and 200 v2 responses. The verifier uses 2,000 requests and a practical 7–13 percent canary acceptance band. It prints actual counts and fails outside that band; measured samples are not repeatedly retried until one passes.

## 7. Observe both releases in Prometheus

Keep this running in a separate terminal:

```bash
./scripts/port_forward_prometheus.sh 19090
```

Open `http://127.0.0.1:19090` and query:

```promql
# Both releases should show positive rates under paced Gateway traffic.
canary:request_rate_60s

# Healthy error percentages should be zero.
canary:error_percentage_60s

# Cumulative business requests, aggregated across replicas.
sum by (version) (requests_total{job="canary-application-pods",method="GET",route="/"})

# No firing error alert is expected in this healthy test.
ALERTS{alertname="CanaryErrorRateHigh",alertstate="firing"}
```

Client response counts provide the primary split evidence. Prometheus also includes earlier and direct Service requests; metrics identify versions but do not label Gateway origin. Run on an otherwise quiet cluster so the counter increases can be associated with the test traffic.

Prometheus needs scrapes before and after requests to calculate useful counter rates. The verifier checks target readiness and sends paced requests across multiple scrapes. After returning to 100/0, v2's rolling rate can remain positive briefly because it describes the trailing 60 seconds. Fresh Gateway requests prove immediate restoration.

## 8. Full live verification

```bash
python3 -u scripts/verify_canary_traffic.py
```

The workflow is:

1. Check application readiness, healthy failure settings, and Prometheus targets by version.
2. Apply 100/0, wait for observed convergence, and require 200 fresh v1-only HTTP 200 responses.
3. Apply 90/10 and wait until Gateway requests reach both versions.
4. Measure 2,000 responses; require all HTTP 200 and 7–13 percent v2.
5. Send 800 paced requests over approximately 80 seconds.
6. Confirm both Prometheus counters increased, both rates are positive, both error percentages are zero, and no canary error alert is firing.
7. Restore 100/0 and verify 200 fresh v1-only responses.

Expect a few minutes, depending on the local cluster. Success requires the final `PASS Phase 5` line and exit code zero.

Once route modification begins, restoration runs even after assertions fail or ordinary interruption (Ctrl+C/SIGTERM). If restoration fails, the script reports it explicitly and prints the recovery command. Forced termination, host shutdown, or an unreachable cluster can prevent cleanup; inspect the route before resuming. Temporary port-forwards are closed.

The earlier `python3 scripts/verify_kubernetes.py` checks the stable-only baseline and should be run after restoring 100/0. It intentionally fails its Gateway version assertion during an active 90/10 split. The normal Phase 4 test remains `python3 scripts/verify_monitoring.py`.

## 9. Troubleshooting

| Symptom | Action |
| --- | --- |
| No ready endpoints | Inspect the affected Deployment, pods, and EndpointSlices before routing traffic there |
| ImagePullBackOff | Re-import local images with `.tools/bin/k3d image import application:v1 application:v2 --cluster canary-mvp`; inspect rollout events |
| Route acceptance timeout | Describe `httproute/application-route` and inspect Gateway/controller status in the explicit context |
| Concurrent route change | Stop competing writers, inspect current weights, and retry the intended command |
| All traffic reaches v1 | Check weights and use `--target gateway`; direct stable traffic bypasses the split |
| Alert during healthy test | Check failure settings and competing traffic; allow prior Phase 4 failures to age out |
| Correct weights but HTTP errors | Inspect statuses per version and application health; configuration acceptance does not prove application health |
| Unexpected return to baseline | Reapplying the HTTPRoute or running Kubernetes deployment resets weights to 100/0 |
| Restoration failed | Run `python3 scripts/set_canary_traffic.py --canary-percent 0`, then inspect fresh Gateway responses |

## 10. Validated results and completion

The complete live verifier passed on September 13, 2026:

| Check | Observed result |
| --- | --- |
| Initial baseline | 200/200 HTTP 200 responses from v1 |
| 90/10 distribution | 1,783 v1 responses (89.15%); 217 v2 responses (10.85%) |
| Response health | 2,000/2,000 HTTP 200, zero transport errors, zero unexpected responses |
| Monitoring | Both request counters increased, both rates positive, both errors zero, no firing canary error alert |
| Restoration | 200/200 fresh HTTP 200 responses from v1 |
| Final state | 100/0 stable baseline; verifier exited zero |

These are recorded results from this local run, not a promise that subsequent samples will have identical counts. Re-run the verifier after environment changes.

Phase 5 establishes working traffic control and observation. Phase 6 will connect health evaluation to automatic traffic recovery. Phase 7 will verify the integrated healthy-release and failed-release workflows, including automatic restoration.
