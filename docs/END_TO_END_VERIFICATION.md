# Phase 7: Integrated MVP verification

## Purpose and scope

Phase 7 exercises the assembled local Canary Rollback Engine: the application, Kubernetes workloads and Gateway, Prometheus health signals, and the automatic recovery controller. It checks repeated release attempts and selected live fault conditions, saving evidence before restoring the baseline.

This is acceptance of the existing local MVP, not certification for production. The fixed local cluster is k3d-canary-mvp. The tests preserve the application Deployments and monitoring storage; they temporarily change traffic weights, canary failure settings, and two recovery-controller endpoint overrides.

See [Phase 6 operations](AUTOMATED_RECOVERY.md), [Phase 5 routing](CANARY_TRAFFIC.md), and [Phase 7 issue history](troubleshooting/PHASE_7_END_TO_END.md).

## Entry point

From the repository root:

```bash
python3 -u scripts/verify_mvp.py
```

The default run performs two complete healthy/recovery cycles plus additional fault scenarios. Increase repetition with:

```bash
python3 -u scripts/verify_mvp.py --cycles 3
```

Fewer than two cycles are rejected. This prevents a shortened run from being presented as the repeatability acceptance test.

To select a new report directory:

```bash
python3 -u scripts/verify_mvp.py --report-dir /tmp/canary-mvp-acceptance-run
```

The directory must not already exist. Otherwise the runner creates a timestamp/PID directory under reports/. Each run preserves its own files. Generated reports are ignored by Git; retain a selected report separately when sharing release evidence.

Allow approximately 20–30 minutes on this local setup. Real observation windows, pod replacement, repeated traffic, and fault recovery dominate the runtime. This is not a one-request smoke check. Individual API/rollout/event waits are bounded, and the traffic generator has finite request counts and per-request timeouts; a heavily degraded environment can take longer.

## Preconditions

- v1 has three ready replicas; v2 has one; the recovery controller has one.
- Both application Deployment templates explicitly specify FAILURE_RATE=0.
- Gateway weights are 100/0.
- Prometheus is reachable and scrapes three v1 targets and one v2 target.
- The controller uses its repository-default endpoints, without PROMETHEUS_URL, GATEWAY_URL, or KUBERNETES_URL overrides.
- Docker/k3d installation and the application/controller images were provisioned by earlier phase setup. Images must be available on every schedulable node because the tests replace pods.
- No independent failure injector, route writer, or application load test is running.

After restoring or changing the local cluster, re-import the existing images before running acceptance:

```bash
.tools/bin/k3d image import application:v1 application:v2 recovery-controller:phase6 --cluster canary-mvp
```

This copies locally built images to the cluster nodes; it does not rebuild them. If a local image is missing, use the earlier application/controller build guides first. Preflight checks running workloads and their image IDs; it does not inspect the image cache of every node. A ready controller on one node does not prove that a replacement can start on another. See [P7-07](troubleshooting/PHASE_7_END_TO_END.md#p7-07-replacement-controller-could-not-pull-its-local-image).

Preflight records route identity/generation, workload readiness, image names and running image IDs, failure settings, repository head, and working-tree status. It renders the base, monitoring, and recovery Kustomize bundles and checks that the baseline route file includes 100/0 weights.

Rendering is a setup-consistency check; it is not a clean installation test. The report explicitly marks fresh-cluster bootstrap as not exercised. No cluster is deleted or rebuilt by this runner.

A file lock prevents concurrent verify_mvp processes in the same checkout. It does not block other scripts, another checkout, or manual kubectl operations; avoid those during verification.

## Scenario sequence and expected evidence

| Scenario | Live action | Pass evidence |
| --- | --- | --- |
| Preflight | Inspect resources, render manifests, sample baseline | Ready workloads, correct targets, successful v1-only responses |
| Insufficient traffic | Activate canary without generating business traffic | After the observation window: unknown with rate below 0.05, no healthy decision, weights retained |
| Prometheus unavailable | Override only the controller's Prometheus URL to localhost port 1 | Connection error reported; active weights retained; no false healthy decision |
| Monitoring resumes | Restore original controller environment | New observation starts; paced requests lead to healthy decision |
| Restart and manual change | Restart observing controller, then change canary weight to 20 | New observation after restart and new route generation; full 60 seconds before healthy decision; 20 retained |
| Healthy cycle 1 and 2 | Verify 90/10 with 2,000 paced requests, then increase to 80/20 with 1,200 requests | All HTTP 200; canary shares 7–13% then 15–25%; healthy decisions for both generations after observation |
| Recovery cycle 1 and 2 | Inject 100-percent v2 failures and activate 90/10 | Real v2 500s, controller-generated verified marker for this route generation, stable-only recovery |
| Pending recovery restart | Block controller-to-Gateway verification, allow real rollback, then restore endpoint | Real pending marker, no false verified event, replacement controller completes pending verification |
| Interruption cleanup | Signal a dedicated helper after it activates 90/10 | Helper exits 130 after restoration; independent stable traffic check passes |

The outage test does not shut down Prometheus or delete its data. It makes Prometheus unreachable specifically to the recovery controller. The report and history use this precise scope when describing the result.

## Healthy release and distribution measurement

The healthy cycle sends 2,000 requests with a 0.065-second scheduled interval, providing traffic across more than two 60-second windows. It checks actual HTTP status and version identity, not just the route configuration.

The 7–13 percent canary acceptance band allows finite-sample variation around 10 percent. Failures, malformed responses, or transport errors do not get removed from the denominator to make the ratio look correct. Each measured cycle must pass; the runner does not repeat a failed sample until it gets a favorable one.

Prometheus metrics are version-scoped, not labeled by Gateway origin. To associate measurements with these requests, use an otherwise quiet environment. After the healthy 10-percent decision and metrics checks, the runner acts as the operator and increases canary traffic to 20 percent. It sends 1,200 requests at a 0.067-second interval, requires a 15–25 percent observed canary share with all HTTP 200 responses, and checks a healthy decision at least 60 seconds after the new generation's observation starts. This explicitly exercises the original plan's successful-release traffic increase. Healthy means remain at the active split; automatic promotion to 100-percent v2 is not part of this MVP.

## Automatic recovery and continued service

For each failure cycle, the runner returns to baseline, sets v2's failure rate to 100, waits for the rollout, and activates canary routing. It sends 1,600 paced Gateway requests.

Recovery must be performed by the controller. The runner checks the persisted marker's verified state and expected generation, rather than accepting a leftover marker from an older exercise. It records the controller's trigger rate/error values, request time, and verified event.

After recovery it sends another 300 requests paced over approximately 30 seconds. All must return HTTP 200 from v1. The controller must emit only one verified event for this recovery generation, and the v2 Deployment must remain ready for investigation.

The runner restores healthy v2 before the next cycle. Passing the second cycle provides evidence that old samples, route generations, and recovery markers do not prevent another canary attempt in this tested workflow.

## Deterministic pending-recovery restart

Restarting the controller at a guessed time is not adequate evidence of crash recovery.

Instead, the runner temporarily points only the controller's GATEWAY_URL to an unreachable localhost endpoint. Application Gateway traffic continues normally. The controller can detect bad v2 metrics and patch the route, but its subsequent verification cannot complete.

The runner waits for that real pending marker and confirms no verified event occurred. It saves the pre-restart events. Restoring the original endpoint changes the Deployment template and replaces the controller pod. The new process must read the existing pending marker, verify stable Gateway responses, and mark this same recovery generation verified.

No fake pending annotation is injected.

## Restart observation and manual route changes

A controller restart must emit a new observation_started event for the active generation. The test then changes the split to 80/20, requiring another observation start tied to the new route generation. The first healthy event for that generation must be at least 60 seconds after its observation start.

This tests live re-reading of operator changes. The offline Phase 6 tests separately cover an API patch conflict. The runner does not claim to have reproduced every possible simultaneous-writer interleaving.

## Interruption and cleanup

The interruption scenario uses the full runner's cleanup method in a child process. The parent waits for an explicit readiness message after activation before sending SIGTERM. The child runs the same route, controller-environment, failure-setting, and final-state restoration checks in finally before exiting with interruption status 130. The parent independently samples the restored route.

The child is an internal test helper, not a second full acceptance run. Its hidden command-line mode should not be used as an operator workflow. Only the parent holds the checkout's verification lock.

For the full suite, normal Ctrl+C/SIGTERM causes the current scenario to be marked failed, later scenarios to be marked not_run, evidence to be saved, and outer cleanup to run. Repeated signals are ignored during this final cleanup to give restoration a chance to finish.

Cleanup independently attempts:

1. Restore traffic to 100/0.
2. Restore the controller's original environment values.
3. Restore v2 FAILURE_RATE=0.
4. Verify workload readiness, settings, controller environment, and fresh stable-only responses.

Failure of one cleanup action does not skip attempts at the others. Cleanup errors are reported separately and cause a failing exit. Force-kill, host loss, or an unavailable API can prevent restoration; those conditions cannot be solved by a finally block.

## Report artifacts

Each run creates:

- REPORT.md: readable scenario/status/duration table and overall result.
- report.json: initial state, per-scenario measurements, decisions, route generations, errors, cleanup result, final state, and omitted coverage.
- One scenario.controller.json file per captured scenario: parsed controller events before cleanup.

Additional events before replacing a controller are embedded in the corresponding scenario evidence. These preserve outage/pending details that otherwise disappear from current-pod logs.

The top-level passed field becomes true only if every required scenario is passed and cleanup is passed. A run with not_run scenarios, a preflight failure, interruption, or cleanup failure cannot be labeled successful. Report files are updated after each scenario and again after cleanup.

Reports include namespace resource identities and local repository status; review them before publishing. They do not fetch Kubernetes Secrets or dump environment-file contents.

## Offline checks

```bash
.venv/bin/pytest -q
.venv/bin/ruff check scripts/verify_mvp.py tests/test_mvp_verifier.py
```

The Phase 7 tests cover report acceptance conditions, failures saved before cleanup, interruption classification, preservation of unrelated environment values, cleanup attempts continuing after an earlier error, cleanup eligibility when the first routing mutation fails, and healthy-release gates before and after a traffic increase.

These supplement the application, traffic, and controller tests; they do not substitute for live acceptance.

## Diagnosing a failed run

Start with report.json: identify the first failed scenario, its evidence, and cleanup status. Consult the scenario log artifact before modifying the cluster.

| Failure | Next check |
| --- | --- |
| Preflight rejects baseline | Restore healthy failure settings and 100/0; inspect replica readiness |
| Insufficient-traffic test sees healthy | Find competing traffic or old samples; do not weaken the unknown-state assertion |
| Missing controller event | Check controller pod logs, restart timing, and resource generation |
| Rollout times out | Inspect pod events and local image availability on the assigned node |
| Pending marker never appears | Check injected v2 responses and Prometheus health evidence |
| Pending marker never becomes verified | Check restored Gateway endpoint, current-generation route acceptance, and controller logs |
| Healthy distribution fails | Inspect actual response counts and errors; configuration acceptance alone is insufficient |
| Cleanup fails | Restore route, controller environment, and v2 settings manually, then verify fresh traffic |

See the [phase-by-phase issue index](troubleshooting/README.md) for earlier failures such as scrape-baseline races, terminating-pod selection, and zero-value predicates.

## Completion boundary

Passing the entire suite establishes the tested local MVP workflow and listed live failure cases. It does not establish fresh-cluster installation, high availability, multi-controller leader election, production load capacity, node-loss recovery, or force-kill cleanup. These remain explicit report exclusions.

The measured acceptance results from implementation are recorded after the live run finishes.
