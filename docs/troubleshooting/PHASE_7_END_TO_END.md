# Phase 7: End-to-end verification issue history

Operating reference: [END_TO_END_VERIFICATION.md](../END_TO_END_VERIFICATION.md).

This file distinguishes implementation safeguards from observed incidents. The acceptance guide records measured live results; the entries below explain changes made during implementation and completion.

## P7-01: A failed or interrupted run must not look complete

**Classification:** implemented safeguard, covered by offline tests.

**Risk:** successful earlier scenarios or a successful final command could conceal a later test failure or failed cleanup.

**Correction/design:** every scenario has a status, error, elapsed time, and evidence record. Later scenarios are not_run after a failure. The overall pass predicate requires every scenario and final cleanup to pass. Reports are saved before restoration.

**Verification:** offline tests cover failed/interrupted scenarios, incomplete runs, and cleanup errors.

**Prevention:** retain the explicit pass predicate and distinguish scenario failure from restoration failure. Never infer acceptance from a single PASS line.

## P7-02: Restarting at a guessed instant is insufficient evidence

**Classification:** implemented live-test synchronization.

**Risk:** a controller restart may happen before rollback or after verification, proving neither persistence nor resumed pending recovery.

**Correction/design:** temporarily block only the controller's Gateway verification endpoint. Wait for a real pending annotation written by the unhealthy-canary action, confirm no false verified event, then restore the endpoint to trigger pod replacement. Require verification of that same generation.

**Prevention:** keep fault injection narrowly scoped; do not fabricate the annotation that the controller is supposed to persist.

## P7-03: Formatting warning in the report writer

**Classification:** observed development issue; corrected.

**Symptom:** E501 on a long report-description string after initial formatting.

**Cause:** a string exceeded the 100-character repository limit; formatting alone did not split it.

**Correction:** shortened the description and reran targeted lint successfully.

**Verification:** the initial expanded offline suite passed 60 tests with two existing dependency warnings; lint passed after the correction.

**Prevention:** inspect both lint and test outcomes. A passing suite does not imply formatting checks passed.

## Remaining evidence

Fresh-cluster bootstrap and destructive environment-loss scenarios are not claimed as tested. The current runner renders manifests and validates the existing local setup. Reports explicitly identify omitted coverage.

## P7-04: Healthy release did not explicitly test the subsequent increase

**Classification:** acceptance coverage gap found during completion review.

**Cause:** the original successful-release scenario includes increasing traffic after healthy metrics. The initial suite tested a healthy 10-percent release and a separate manual 20-percent change, without requiring the increase to follow the healthy decision.

**Correction:** each healthy cycle now verifies the 10-percent decision and metrics before the runner performs an operator-controlled increase to 20 percent. Fresh traffic must pass the distribution and HTTP checks, and the new generation must finish its own 60-second observation before its healthy decision is accepted.

**Verification:** offline tests cover successful progression, HTTP failures before and after the increase, and premature evaluation. The full live suite exercises the sequence twice. See the acceptance record for the live outcome.

**Scope:** automatic traffic promotion remains future work; this closes the MVP operator workflow without adding a second deployment decision engine.

## P7-05: Cleanup flag was set after the first routing mutation

**Classification:** implemented safeguard found during review, covered by an offline regression test.

**Risk:** the first baseline route patch could succeed but its acceptance wait could fail before activation set the mutation flag. Outer cleanup would then be skipped.

**Correction:** mark the suite as requiring cleanup before its first routing mutation in the insufficient-traffic scenario.

**Verification:** a simulated patch/status failure confirms that cleanup remains required. This test does not claim a live Kubernetes API outage was injected.

## P7-06: Completion notes and architecture described different scopes

**Classification:** observed documentation inconsistency; corrected during completion.

**Symptom:** README still marked Phase 7 as pending, while the saved initial run passed. The technology table and architecture diagram also mixed the existing Python controller and Gateway implementation with future Argo Rollouts, CI/CD, and Grafana plans.

**Correction:** document the implemented local architecture, identify roadmap components explicitly, connect the seven phase guides, and retain a selected acceptance record in version control. Per-run reports remain ignored.

**Verification:** repository lint passed after formatting existing monitoring-verifier lines. The final offline suite passed 65 tests with the same two dependency deprecation warnings noted in earlier phases. New test assertions initially expected AssertionError from the reused HTTP validator; they were corrected to its existing RuntimeError contract before this passing run.

## P7-07: Replacement controller could not pull its local image

**Classification:** observed live environment failure on September 13, 2026.

**Evidence:** run `mvp-20260913T164316-68606` passed preflight and insufficient traffic, then blocked while replacing the controller for the Prometheus-outage scenario. The replacement on `k3d-canary-mvp-agent-0` entered ImagePullBackOff for `recovery-controller:phase6`; Kubernetes reported `pull access denied, repository does not exist or may require authorization` when attempting Docker Hub.

**Cause:** the local controller image was unavailable to the replacement on that node. The original controller was ready, so the running-workload preflight could not detect this cache gap. Why that node no longer had the image was not established.

**Correction:** interrupted the run with SIGTERM and re-imported the existing controller and application images to both cluster nodes:

```bash
.tools/bin/k3d image import recovery-controller:phase6 application:v1 application:v2 --cluster canary-mvp
```

**Verification:** k3d reported successful imports on server and agent. The interrupted attempt exited 1, recorded the active scenario as failed and remaining scenarios as not_run, and completed cleanup successfully. A fresh full acceptance run was then started; its outcome is recorded separately in the acceptance evidence. The failed attempt is not counted as a pass.

**Prevention:** re-import locally built images after cluster restoration or cache changes. Keep the all-node image prerequisite explicit. Existing `scripts/deploy_recovery.sh` already imports the controller image during setup; the acceptance runner does not rebuild images or certify fresh installation.
