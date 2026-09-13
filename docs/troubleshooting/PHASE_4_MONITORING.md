# Phase 4: Monitoring failures, investigation, and corrections

Operating reference: [Monitoring guide](../MONITORING.md).
Implementation: [monitoring verifier](../../scripts/verify_monitoring.py), [Prometheus configuration](../../infra/kubernetes/monitoring/prometheus.yaml).
Evidence: failed and successful live runs preserved in the implementation conversation, inspected source, and recorded Prometheus query results.

## Investigation overview

Several independent failures appeared while trying to prove the same end-to-end monitoring exercise. They must not be collapsed into one root cause.

| Observation | Finding | Outcome |
| --- | --- | --- |
| Rollout timed out | Image-pull failure in replacement pod | Local mitigation; see Phase 3 |
| No high-error rate after immediate test traffic | Missing pre-traffic scrape baseline was a plausible race | Wait for exact replacement target |
| A 95-percent assertion failed | Assertion did not match the five-percent policy | Align assertion with policy |
| Recovery waited for an old pod | First listed pod could be terminating | Select active Ready pod |
| Recovery timed out while API showed zero | Zero was replaced with 100 by Python truthiness | Compare explicitly with zero |
| Longer recovery allowance still failed | Timeout changes did not correct the predicate | Retain as rejected root-cause hypothesis |

## P4-01: Zero was converted to 100 in the verifier

**Classification/status:** confirmed verifier bug; corrected.

**Context:** after restoring healthy v2, the test waits for error percentage zero and a cleared alert.

**Symptom:**

> RuntimeError: Canary error signal did not clear after the 60-second evaluation window

Manual queries later returned v2 error percentage 0 and no CanaryErrorRateHigh alert.

**Root cause:** the old predicate was:

```python
lambda: (error_signal() or 100) == 0
```

Python considers 0.0 false, so a correct zero result became 100. The predicate could not succeed for the intended recovery value.

**Investigation and unsuccessful attempts:** waits were increased from 100 to 150, then 240 seconds. Earlier explanations blamed rolling-window timing or a Prometheus-series edge case. The preserved code establishes that those explanations did not account for this assertion: more time could not make a zero value pass.

**Correction:**

```python
lambda: error_signal() == 0
```

A missing value remains None and does not pass the explicit comparison. The final successful run used this correction.

**Verification:** the full verifier printed the restored-v2 recovery PASS and exited zero. The final cluster check showed one ready canary, three ready stable pods, and a ready Prometheus with a bound PVC.

**Prevention:** proposed follow-up: unit-test predicates with zero, positive values, None, and nonfinite values. Read the assertion before tuning timeouts. Avoid truthiness-based defaults where zero is a valid measurement.

**Remaining limitation:** the current 240-second allowance was retained from investigation. It is a test timeout, not the alert's 60-second measurement window. It is not evidence that normal metric recovery requires four minutes.

## P4-02: Traffic before the first scrape

**Classification/status:** observed failed rate checks; scrape timing was addressed as an implementation race. Per-scrape timestamps were not captured to establish it as the sole cause of every failed run.

**Context:** after replacing v2, the verifier immediately sent a short burst of failures.

**Symptom:** the verifier did not observe the expected high error percentage. A manual check confirmed ten canary requests all returned HTTP 500 with FAILURE_RATE=100.

**Cause addressed:** Kubernetes readiness does not mean Prometheus has already discovered and scraped that pod. A counter rate needs measurements over time. If the first observed counter value already contains the complete burst, subsequent unchanged values cannot demonstrate those earlier increments.

**Correction:** added wait_for_canary_target after both the failure rollout and recovery rollout, before sending business traffic. It checks up=1 for the selected replacement pod.

**Verification:** subsequent output explicitly showed Prometheus scraping the replacement pod before failure requests and the expected alert. Final complete verification passed after the other independent bugs were also corrected.

**Prevention:** synchronize test traffic with target discovery, especially after process replacement. For sustained monitoring checks, pace requests across multiple scrape intervals, as Phase 5 now does.

**Limitation:** up=1 establishes a successful scrape, not a complete 60-second history. The verifier must still wait for subsequent samples and evaluations.

## P4-03: Terminating pod selected during recovery

**Classification/status:** confirmed verifier selection bug; corrected.

**Symptom:** recovery failed with a message that Prometheus did not discover a replacement pod, but the pod name in the error belonged to the previous failure-mode v2 pod.

**Root cause:** the first implementation selected .items[0].metadata.name from the canary pod list. During rollout overlap, list position did not establish which pod was current. A terminating old pod could be selected.

**Correction:** current_ready_canary_pod now filters for Running phase, no deletionTimestamp, and Ready=True, and requires exactly one candidate. Prometheus discovery is checked for that exact name.

**Verification:** the final run printed different failure-mode and restored-mode target names and completed recovery successfully.

**Prevention:** use lifecycle fields and expected cardinality rather than API-list order. Proposed follow-up: add unit coverage with old terminating pods, missing readiness, and multiple candidates.

**Limitation:** requiring exactly one candidate is appropriate to the current one-replica canary. Scaling canary replicas requires revisiting this verifier; do not reuse the assumption blindly.

## P4-04: Test threshold did not match policy

**Classification/status:** confirmed mismatch in test requirements; corrected. The mismatch alone does not explain runs affected by missing scrape baselines.

**Symptom:** the verifier demanded an error percentage greater than 95 after injecting 100-percent failing traffic.

**Root cause:** FAILURE_RATE=100 controls new request behavior. The rolling error percentage can include recent healthy requests and therefore need not immediately exceed 95. The implemented alert policy is above five percent with sufficient traffic.

**Correction:** replaced the >95 assertion with >5 and separately waited for the firing alert.

**Verification:** later runs printed that the five-percent threshold was exceeded and the expected alert fired.

**Prevention:** keep three values distinct: injected failure probability, observed rolling error percentage, and policy threshold. Test the intended policy and separately inspect response status counts when proving injection.

**Policy nuance:** the current rule's traffic guard is request rate >=0.05 requests/second. Over 60 seconds that corresponds to about three requests, but Prometheus rate extrapolation means it is not an exact integer request-count gate. No traffic must not be taken as affirmative evidence of a healthy release. This documentation does not change the rule.

## P4-05: Process output and progress

**Classification/status:** observed tooling/observability problem; corrected workflow and improved progress output.

**Symptom:** early execution produced no captured final pass result. A follow-up wait used an already-closed execution-cell identifier and returned exec cell not found.

**Cause:** an outer tool execution ID and the shell process's session ID have different lifetimes. Discarding process metadata or polling the wrong handle loses verification evidence. Buffered output also made waits opaque.

**Correction:** preserve the shell session_id and poll it through write_stdin; print stage updates with flush=True. Distinguish start, partial output, and confirmed exit status.

**Verification:** later runs captured stage-specific PASS messages and the final exit_code=0.

**Prevention:** never infer success from a ready pod or absence of output. Preserve the completion code. Bound subprocess calls and surface relevant stderr in future verifier improvements.

## P4-06: Development-tool failures

**Classification/status:** observed environment and editing errors; not application failures.

**Symptoms:** ordinary shell/patch calls failed with:

> bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted

A fallback Perl edit also failed with:

> Unknown regexp modifier "/t"

**Causes:** the execution sandbox failed while setting up its environment; the underlying host restriction was not diagnosed. Separately, a replacement expression used conflicting delimiter/escaping syntax.

**Corrections:** commands requiring filesystem access ran through the approved execution path. The failed expression was corrected, and the resulting Python was inspected and syntax-checked. In later Phase 5 work, command apply_patch succeeded through the approved shell and avoided broad substitution edits.

**Verification:** successful edits were followed by source inspection, syntax checks, and ultimately a passing live verifier. A syntax check alone did not detect the zero-value logical bug.

**Prevention:** use narrow patches, inspect their result, and stop dependent operations after an edit error. Separate tool failure from project failure in status updates. Do not infer that a permission denial means the repository itself is broken.

## Additional limitations exposed by reviewing current code

These are current safeguards to improve, not claimed resolved incidents:

- The Phase 4 discovery check counts four up targets in total; its PASS wording is stronger than the assertion because it does not independently count three v1 and one v2. Phase 5 checks counts by version.
- Phase 4 cleanup catches and suppresses CalledProcessError while attempting to restore FAILURE_RATE=0. A cleanup failure can therefore be less visible than intended. Proposed follow-up: surface restoration failures explicitly and verify final state.
- Recording zero error percentage alone cannot establish health without real traffic and a sufficient observation interval.
- Pinning the canary to a node does not prove its local image will always remain available. See [the infrastructure incident](PHASE_3_KUBERNETES.md#p3-01-local-image-pull-failures-during-phase-4).

## Final recorded verification

The complete exercise eventually passed all stages: four discovered pods, healthy zero-error signals, failing v2 alert, restored v2 at zero, and cleared alert. The final correction was the zero-value predicate; earlier rollout, target selection, and scraping corrections addressed separate obstacles.

No failure-injection exercise was rerun solely to write this history. Use the operating guide's complete verifier when a new implementation or environment change requires revalidation.
