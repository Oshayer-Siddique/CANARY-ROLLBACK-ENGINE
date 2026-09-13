# Phase 5: Canary traffic issues and safeguards

Operating reference: [Canary traffic guide](../CANARY_TRAFFIC.md).
Evidence: [traffic command](../../scripts/set_canary_traffic.py), [generator](../../scripts/generate_traffic.py), [verifier](../../scripts/verify_canary_traffic.py), [tests](../../tests/test_canary_traffic.py), and the recorded implementation run.

Phase 5's first complete live routing exercise passed. The records below distinguish gaps corrected while extending the project, preventive safeguards, and actual editing/test-tool failures. There is no evidence of a Phase 5 production traffic outage.

## P5-01: Existing generator could not measure the split

**Classification/status:** observed capability gap in the starting source; implemented and validated.

**Context:** Phase 4 generated direct traffic to stable or canary Services. Phase 5 needed evidence that shared Gateway routing actually distributed requests.

**Symptom:** the old generator accepted only stable/canary targets and reported a set of versions, not counts per version. It could show that v2 existed but could not prove a 90/10 Gateway split.

**Cause:** the tool was designed for isolated monitoring checks, not routing-distribution verification.

**Correction:** added gateway target for the NGINX data-plane Service; version counts and percentages; per-version HTTP status counts; transport/unexpected-response accounting; and a pacing interval. Existing direct targets remain available.

**Verification:** the live 2,000-request sample returned 1,783 v1 and 217 v2 responses (89.15/10.85), all HTTP 200, with zero transport errors and unexpected responses. Paced traffic then produced positive Prometheus request rates and zero errors for both versions.

**Prevention:** measure at the actual path being tested. Use client identity counts for distribution and Prometheus as corroborating monitoring evidence. Preserve all attempted requests in the denominator so failures are not hidden.

**Limitations:** a direct Service port-forward selects a pod; it is not a replica-distribution test. Prometheus series identify versions but not Gateway origin, so concurrent direct traffic can contaminate attribution.

### Why a nonexact split is not a bug

The verifier accepts 7–13 percent canary in a 2,000-request sample instead of requiring exactly 200 v2 responses. The recorded 217 responses are inside the planned tolerance. This is a practical distribution check, not a guarantee of exact routing percentages in every short sample.

## P5-02: Routing safety and restoration

**Classification/status:** implemented safeguards, not a claim that each risk occurred.

**Risks addressed:** stale Accepted status could be mistaken for processing a new route; competing writers could overwrite weights; requiring healthy canary endpoints could prevent restoring stable traffic after canary failure.

**Correction:** the traffic command uses a resource-version precondition and updates both weights together. It checks current-generation Accepted/ResolvedRefs for the intended Gateway parent. Ready endpoints are required only for positive-weight backends. Canary Service existence and port remain required even at zero weight.

**Verification:** offline tests reject invalid percentage values, reject old-generation conditions, verify weight boundaries, and verify that stable restoration does not require canary endpoints. The live run verified 100/0, 90/10, and fresh-request restoration.

**Prevention:** retain control-plane checks and real request checks together. Keep the restoration path less dependent on canary health than the activation path. Use explicit context and namespace.

**Limitations:** the single-rule route layout is deliberate; the command refuses other layouts. A timeout can occur after a patch applied. Kubernetes API unavailability can prevent cleanup. The standalone command does not automatically undo an accepted traffic change.

### Baseline compatibility

The checked-in route is 100/0, and reapplying it resets active routing. The original Phase 3 verifier expects v1-only Gateway responses; running it at 90/10 would intentionally violate that assertion. The new operating guide documents the required baseline rather than weakening the old verifier.

The complete Phase 5 verifier restores baseline in finally, verifies fresh requests, and reports restoration failure explicitly. Ctrl+C/SIGTERM are handled through normal cleanup; force-kill or host shutdown cannot guarantee cleanup. No deliberate interruption test was recorded, so that behavior is implementation-reviewed rather than separately live-proven.

## P5-03: Patch format and lint failures

**Classification/status:** observed development-tool errors; corrected.

**Symptoms:** the first draft produced four E501 line-length errors. A later large documentation/test patch returned:

> Invalid patch: The first line of the patch must be '*** Begin Patch'

The expected new test file did not exist, so formatting/lint reported a missing file. The shell still ran the existing pytest suite, which passed 19 tests.

**Causes:** initial Python formatting exceeded the configured 100-character limit. Separately, the patch body omitted its required opening marker. The command sequence did not stop after that patch failure, allowing unrelated existing tests to run.

**Impact:** the new documentation and tests had not been created despite the existing suite passing. That 19-test result was not evidence that new Phase 5 safety tests passed.

**Correction:** formatted the Python code, resubmitted the patch with its required marker, and reran checks against the actual new files.

**Verification:** the corrected run reported successful file creation, lint success, and 31 passing tests with two existing dependency warnings.

**Prevention:** inspect each tool result and expected file existence; do not equate a shell's final zero exit status with success of earlier commands. Use fail-fast execution for dependent steps. Confirm test count and relevant test names when new tests are introduced.

## P5-04: Distinguishing measurement history from restoration

**Classification/status:** anticipated interpretation risk addressed in documentation; no recorded restoration failure.

**Potential symptom:** v2 still has a positive 60-second rate after traffic weights become 100/0.

**Cause:** rolling metrics describe recent history. Already in-flight requests may also complete on their earlier backend.

**Correction/design:** wait for observed data-plane convergence and then measure fresh Gateway requests. Do not use immediate zero rolling request rate as the restoration assertion.

**Verification:** after restoring baseline, the live test returned 200/200 fresh HTTP 200 responses from v1. The original Kubernetes and normal monitoring verifiers subsequently passed.

**Prevention:** distinguish route configuration, new client responses, and trailing-window metrics in operational dashboards and future recovery assertions.

## Recorded completion evidence

| Check | Result |
| --- | --- |
| Baseline | 200 fresh v1 responses, all HTTP 200 |
| Weighted sample | v1: 1,783; v2: 217; no transport/response errors |
| Monitoring | Both counters increased, both rates positive, both errors zero, no firing error alert |
| Restoration | 200 fresh v1 responses, all HTTP 200 |
| Offline checks | 31 passed; two pre-existing dependency warnings |
| Earlier live regression checks | Kubernetes stable-only and normal monitoring verifiers passed |

These results came from the implementation run on September 13, 2026. They were not rerun during documentation work. Phase 6 automatic recovery and Phase 7 integrated end-to-end verification remain separate future work.
