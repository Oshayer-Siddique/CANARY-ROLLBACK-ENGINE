# Phase 6: Automated recovery

Status: implemented and live-validated on September 13, 2026. The controller remains deployed; the final tested state is healthy v2 with stable-only Gateway routing.

## Purpose

Phase 6 connects the Phase 4 health measurements to the Phase 5 traffic route. One Python controller runs inside Kubernetes, observes an active canary, and restores 100-percent stable traffic when v2 exceeds the MVP error policy. The canary Deployment is retained for investigation.

Phase 7 remains the integrated end-to-end verification phase. Phase 6's focused tests do not replace that final acceptance work.

## Architecture

```text
Gateway traffic -> stable v1 / canary v2
                         |
                  application metrics
                         |
                     Prometheus
                         |
                 recovery-controller
                         |
          evaluate -> patch application-route
                         |
            verify fresh stable responses
```

The controller uses Python's standard library, Kubernetes HTTPS API with its mounted ServiceAccount token and CA, Prometheus's internal HTTP API, and the internal NGINX Gateway Service. It does not need kubectl or Docker inside its container.

## Files

| File | Responsibility |
| --- | --- |
| recovery/controller.py | Pure policy function, API clients, recovery state machine, JSON logs |
| recovery/Dockerfile | Small non-root controller container |
| recovery/.dockerignore | Restricted controller build context |
| infra/kubernetes/recovery/controller.yaml | ServiceAccount, namespaced RBAC, one-replica Deployment |
| infra/kubernetes/recovery/kustomization.yaml | Deployment resource bundle |
| scripts/deploy_recovery.sh | Build, import into both local nodes, apply and restart controller |
| scripts/verify_recovery.py | Healthy and failing live canary exercise with baseline cleanup |
| tests/test_recovery.py | Policy boundaries and controller error/restart tests |

The controller uses its own build context, so the application Dockerfile and its allowlist remain unchanged.

## Policy and states

The default MVP policy is intentionally fixed in code:

- Five-second delay between evaluation attempts; API calls add to the actual cycle time.
- Full 60-second observation wait after first seeing an active route generation.
- Existing 60-second Prometheus recording rules for v2.
- Error percentage strictly greater than 5 triggers recovery; exactly 5 does not.
- Request rate must be at least 0.05 requests/second.
- At least one fresh, successfully scraped v2 target must exist.
- Recording-rule samples older than 15 seconds are excluded.
- Missing, nonfinite, negative, or out-of-range error measurements are not healthy evidence.

The traffic guard matches the existing alert's rate threshold. It corresponds approximately to three requests over 60 seconds, not an exact integer count due to rate estimation.

| Log state | Meaning |
| --- | --- |
| baseline | Canary weight is zero; no active recovery needs verification |
| observation_started / observing | New route generation detected; waiting the full observation period |
| unknown | Missing/invalid values, inadequate traffic, or no successful canary target |
| healthy | Sufficient evidence and error percentage no greater than 5 |
| unhealthy | Policy breached; recovery will be attempted |
| rollback_requested | Optimistic route patch succeeded; data-plane verification remains |
| recovery_waiting_for_route | Waiting for current-generation Accepted/ResolvedRefs |
| rollback_verified | Twenty fresh Gateway requests returned HTTP success and v1 |
| controller_error | API, parsing, conflict, availability, or verification failure; retry follows |

An unreachable Prometheus produces controller_error; it never becomes a fabricated healthy result. Unknown/error states retain current weights and retry. This is an explicit MVP policy: monitoring failure is not an automatic rollback trigger.

## Recovery transaction and restart behavior

1. Read the single-rule application-route and validate its two Service backend references.
2. Identify the route by UID and generation. A new generation restarts observation.
3. Require controller acceptance of the current route and fetch fresh health signals.
4. If unhealthy, confirm at least one available stable Deployment replica.
5. Patch both weights to 100/0 and write a pending recovery annotation in one request, guarded by the read resourceVersion.
6. On later iterations, wait for the recovery route generation to be accepted.
7. Request the Gateway twenty times. Any HTTP failure or non-v1 response fails verification.
8. Mark the annotation verified and emit rollback_verified.

The annotation key is canary-mvp/recovery. It records pending/verified state, expected generation, trigger measurements, and request time. This allows a restarted controller to resume pending verification without claiming that a patch alone completed recovery. A new active canary configuration starts a new observation period.

No Deployment, image, pod, or failure-rate setting is changed by the controller. If Gateway verification fails after the patch, weights remain 100/0 and verification retries. The annotation is a latest-action marker, not a durable multi-release audit database.

Concurrent changes cause an optimistic patch failure. The next cycle re-reads the route. An externally superseded recovery generation is not stamped verified for that older action.

## Permissions and deployment

Prerequisites: the Phase 5 route with both backends, healthy stable workload, Prometheus, Docker, kubectl, and the local k3d binary.

```bash
bash scripts/deploy_recovery.sh
kubectl --context k3d-canary-mvp -n canary-mvp get deployment recovery-controller
kubectl --context k3d-canary-mvp -n canary-mvp logs deployment/recovery-controller --tail=30
```

The ServiceAccount can get/patch only application-route and get only application-v1 Deployment in canary-mvp. It cannot modify Deployments, delete pods, read secrets through the API, or patch arbitrary routes. Its mounted identity token is used only for Kubernetes API calls.

The Deployment uses one replica with Recreate updates. The container is non-root, read-only, and drops Linux capabilities. This MVP is single-writer; do not scale the controller to multiple replicas without leader election.

The deployment script builds recovery-controller:phase6 locally and imports it to the cluster. Re-running it rebuilds and restarts the controller; an active canary then receives a fresh observation wait.

## Operating flow

Start from healthy application settings and activate canary traffic:

```bash
python3 scripts/set_canary_traffic.py --canary-percent 10
python3 scripts/generate_traffic.py --target gateway --requests 1800 --interval 0.067
```

Follow decisions in another terminal:

```bash
kubectl --context k3d-canary-mvp -n canary-mvp logs -f deployment/recovery-controller
```

A healthy canary remains active; there is no automatic promotion. A sufficiently unhealthy canary is removed from new Gateway traffic.

Manual stable restoration remains available:

```bash
python3 scripts/set_canary_traffic.py --canary-percent 0
```

Inspect the durable latest recovery marker:

```bash
kubectl --context k3d-canary-mvp -n canary-mvp get httproute application-route -o yaml
```

A manual restore without a pending controller action is baseline, not a controller-generated rollback_verified event.

## Verification

```bash
.venv/bin/pytest -q
python3 -u scripts/verify_recovery.py
```

The live verifier requires the controller to be running. It restores healthy v2, activates 90/10, and generates approximately 100 seconds of healthy traffic. It checks that v2 remains active and controller logs include a healthy evaluation.

It then returns to baseline, injects FAILURE_RATE=100 into v2, reactivates 90/10, and generates approximately 120 seconds of traffic. It requires observed v2 HTTP 500 responses and a verified controller annotation tied to this activation's generation. Finally it checks 200 fresh v1-only HTTP 200 responses.

The test's finally block restores 100/0 and FAILURE_RATE=0. Do not run concurrent traffic writers or Phase 4 failure tests. Normal process interruption runs cleanup; force-kill, host loss, or cluster API unavailability can prevent it. Inspect final state after an interrupted run.

Offline tests cover exact policy boundaries, missing/nonfinite data, observation delay, generation changes, patch conflicts, unavailable stable deployment, failed Gateway verification, and restart of pending recovery.

## Interaction with earlier phase tests

Phase 5 healthy verification is compatible with the controller because healthy canaries remain active. Phase 4's failure exercise should run at 100/0 so the controller remains idle. When troubleshooting monitoring independently, suspend the controller explicitly if necessary:

```bash
kubectl --context k3d-canary-mvp -n canary-mvp scale deployment/recovery-controller --replicas=0
# Resume after the isolated exercise.
kubectl --context k3d-canary-mvp -n canary-mvp scale deployment/recovery-controller --replicas=1
```

Scaling does not itself restore traffic. Check weights first. The controller is not an emergency substitute for a healthy stable release.

## Retry behavior and known limits

Every API operation has a five-second timeout. Errors are logged and retries occur on subsequent cycles with a five-second delay. Retries continue indefinitely to permit recovery after transient outages; there is no silent terminal success or bounded total retry count.

The default URLs can be overridden by PROMETHEUS_URL, GATEWAY_URL, and KUBERNETES_URL. Thresholds, window, and poll delay are currently code constants aligned to the existing recording rules; changing them requires coordinated code/rule changes and tests.

The metrics are version-scoped, not release-ID or Gateway-origin scoped. Avoid concurrent direct canary traffic and overlapping v2 release experiments. A route generation change restarts observation, but changing the v2 Deployment alone does not. Controller restart also restarts active observation, conservatively delaying evaluation. Pending recovery verification survives through the annotation.

Deployment availableReplicas is a preliminary stable availability check; fresh Gateway requests provide the final behavioral proof. Twenty requests are finite evidence, not an absolute proof that no future request could fail.

The process runs continuously and Kubernetes restarts it if it exits. There is no separate HTTP readiness endpoint proving Prometheus connectivity; Deployment readiness alone is not evidence that health evaluation is working. Inspect decision logs and run the verifier.

For recorded implementation issues and follow-up prevention, see [Phase 6 issue history](troubleshooting/PHASE_6_AUTOMATED_RECOVERY.md).

## Recorded validation results

The full live verifier exited zero. Healthy v2 stayed active at 90/10 after the observation period. During the failing exercise, the controller measured 100-percent v2 errors at approximately 1.336 requests/second, patched route generation 7 to generation 8, and logged rollback_verified after its twenty fresh stable requests. The independent verifier then received 200/200 HTTP 200 responses from v1, with zero transport errors and no unexpected responses.

Cleanup successfully restored FAILURE_RATE=0 and 100/0 routing. All 49 offline tests passed, targeted lint passed, and the deployment script passed shell syntax checking. Two existing dependency deprecation warnings remain. Live RBAC checks returned yes for patching the named application route and no for patching Deployments.

Missing-data, API-conflict, restart, stable-unavailability, and failed-verification paths were covered by offline tests; they were not all fault-injected into the cluster in this run. Phase 7 remains the broader integrated acceptance exercise.
