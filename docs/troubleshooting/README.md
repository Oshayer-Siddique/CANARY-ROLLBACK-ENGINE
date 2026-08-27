# Phase-by-phase issue history and troubleshooting

This collection records the problems encountered while building the Canary Rollback Engine: symptoms, causes, investigation, corrections, verification, and prevention. It complements the operating guides rather than replacing them.

Compiled September 13, 2026 from the available implementation conversation, repository history, current code, tests, and existing phase guides. No cluster changes or failure injections were performed to write these documents.

## How to interpret evidence

- **Observed incident:** an error or failure is preserved in the implementation conversation or repository history.
- **Implemented safeguard:** current code handles a risk, but no original incident log establishes that it actually happened.
- **Known limitation:** current behavior or remaining risk, not a claim that a failure occurred.
- **Proposed prevention:** recommended follow-up; not implemented unless explicitly stated.

Phases 1–3 do not have a complete preserved debugging transcript in the evidence reviewed. Their files distinguish recorded facts from preventive guidance. We cannot honestly claim to have reconstructed every historical error. Phase 4 has multiple recorded failed runs; Phase 5 has a successful live run and recorded tooling corrections.

Conversation evidence is described inside each incident because it is not a committed log artifact. Repository links point to current implementation, which can change. Recorded test results describe their original runs, not a fresh execution during documentation work.

## Phase index

| Phase | Issue history | Operating guide |
| --- | --- | --- |
| 1: Application | [Application issues](PHASE_1_APPLICATION.md) | [Application guide](../APPLICATION_DETAILED_GUIDE.md) |
| 2: Docker | [Container issues](PHASE_2_DOCKER.md) | [Docker guide](../DOCKER.md) |
| 3: Kubernetes | [Cluster and routing issues](PHASE_3_KUBERNETES.md) | [Kubernetes guide](../KUBERNETES.md) |
| 4: Monitoring | [Monitoring investigation](PHASE_4_MONITORING.md) | [Monitoring guide](../MONITORING.md) |
| 5: Canary traffic | [Traffic control issues](PHASE_5_CANARY_TRAFFIC.md) | [Canary traffic guide](../CANARY_TRAFFIC.md) |
| 6: Automated recovery | [Recovery issues](PHASE_6_AUTOMATED_RECOVERY.md) | [Recovery guide](../AUTOMATED_RECOVERY.md) |
| 7: End-to-end verification | [Acceptance-test issues](PHASE_7_END_TO_END.md) | [End-to-end guide](../END_TO_END_VERIFICATION.md) |

Phase 6 implementation and validation are recorded in its linked history. Phase 7 now has an acceptance runner and its own issue history; use its report and operating guide for measured completion status.

## Symptom index

| Symptom or concern | Record |
| --- | --- |
| Dependency warnings during passing tests | [P1-01](PHASE_1_APPLICATION.md#p1-01-test-dependency-deprecation-warnings) |
| Duplicate metrics or missing initial error series | [P1-02](PHASE_1_APPLICATION.md#p1-02-metric-isolation-and-zero-series) |
| Health endpoints pass while business requests fail | [P1-03](PHASE_1_APPLICATION.md#p1-03-simulated-failures-and-probes) |
| Environment files included in source control | [P2-01](PHASE_2_DOCKER.md#p2-01-environment-file-tracking) |
| Image tag and returned version disagree | [P2-02](PHASE_2_DOCKER.md#p2-02-image-identity-and-packaging) |
| Replacement pod cannot pull application:v2 | [P3-01](PHASE_3_KUBERNETES.md#p3-01-local-image-pull-failures-during-phase-4) |
| Resource lookup reports NotFound | [P3-02](PHASE_3_KUBERNETES.md#p3-02-wrong-resource-name-during-diagnosis) |
| Recovery times out despite zero-percent metrics | [P4-01](PHASE_4_MONITORING.md#p4-01-zero-was-converted-to-100-in-the-verifier) |
| Failing traffic is not reflected in rates | [P4-02](PHASE_4_MONITORING.md#p4-02-traffic-before-the-first-scrape) |
| Verifier waits for an old canary pod | [P4-03](PHASE_4_MONITORING.md#p4-03-terminating-pod-selected-during-recovery) |
| Failure test demands a 95-percent rolling error rate | [P4-04](PHASE_4_MONITORING.md#p4-04-test-threshold-did-not-match-policy) |
| Lost output or prolonged silence during verification | [P4-05](PHASE_4_MONITORING.md#p4-05-process-output-and-progress) |
| Sandbox or patch commands fail | [P4-06](PHASE_4_MONITORING.md#p4-06-development-tool-failures) and [P5-03](PHASE_5_CANARY_TRAFFIC.md#p5-03-patch-format-and-lint-failures) |
| Distribution is not exactly 90/10 | [P5-01](PHASE_5_CANARY_TRAFFIC.md#p5-01-existing-generator-could-not-measure-the-split) |
| Old route status or unhealthy canary blocks restoration | [P5-02](PHASE_5_CANARY_TRAFFIC.md#p5-02-routing-safety-and-restoration) |

## Common investigation strategy

1. State the expected behavior and identify the phase's scope.
2. Capture the failing command, output, context, affected resource, and timestamp.
3. Separate configuration acceptance, runtime health, client responses, and test assertions.
4. Read the source condition before extending a timeout or modifying infrastructure.
5. Change one cause at a time where possible; record unsuccessful hypotheses explicitly.
6. Repeat the failing check, then a relevant regression check.
7. Restore the intended baseline and record any remaining uncertainty.

Prefer explicit context and namespace in diagnostic commands. A route command can succeed while data-plane requests fail; a verifier can fail while the underlying service is healthy. Neither observation alone proves a root cause.

## Template for future incidents

Use a stable ID such as P6-01 and the following fields:

- Classification and evidence: observed incident, safeguard, or limitation; source and date.
- Context and impact: intended operation and what was blocked.
- Symptom: exact error excerpt when available, without sensitive values.
- Investigation: checks, observations, rejected hypotheses.
- Root cause: confirmed explanation; state uncertainty separately.
- Correction: file, function, configuration, or operational command.
- Verification: exact check, outcome, and what it does not establish.
- Prevention: existing tests/guards versus proposed improvements.
- Status: resolved, mitigated, open, or awaiting evidence.

Do not copy secrets into incident records. Preserve narrowly scoped logs or test summaries when useful. Keep historical failed attempts even after the implementation is corrected.
