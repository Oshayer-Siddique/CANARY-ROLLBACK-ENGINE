# Phase 1: Application issues and safeguards

Operating reference: [Application implementation guide](../APPLICATION_DETAILED_GUIDE.md).
Evidence: [configuration](../../app/config.py), [metrics](../../app/metrics.py), [application tests](../../tests/test_app.py), [metric tests](../../tests/test_metrics.py), and the guide's validation notes.

No detailed original Phase 1 debugging transcript was available in the evidence reviewed. Apart from the recorded warnings below, the following entries describe implemented safeguards rather than reconstructed incidents.

## P1-01: Test dependency deprecation warnings

**Classification/status:** observed warning; unresolved dependency maintenance item, not a failing application test.

**Context and symptom:** the existing application guide records two dependency warnings. The later Phase 5 suite also printed warnings about Starlette's HTTPX test-client support and the AnyIO BlockingPortal alias while all 31 tests passed.

**Cause:** dependency compatibility/deprecation notices in the test stack. They do not demonstrate that runtime requests or metrics failed.

**Investigation:** distinguish the pytest pass/fail result from its warnings summary. The recorded Phase 5 run finished with 31 passed and two warnings.

**Correction:** no dependency migration was performed in the recorded work. Reporting a clean test result must retain the fact that warnings remain.

**Verification:** the passing test suite establishes its covered behaviors; it does not prove compatibility with a future dependency release.

**Prevention:** proposed follow-up: choose compatible dependency versions, migrate the warned interfaces when appropriate, and rerun the suite. Do not suppress warnings or upgrade packages blindly merely to produce a clean-looking log.

## P1-02: Metric isolation and zero series

**Classification/status:** implemented safeguards; no preserved duplicate-collector incident.

**Potential symptom:** multiple application factories sharing a registry can collide on metric definitions or mix measurements; an absent error series can make a healthy release's query appear missing rather than zero.

**Cause addressed:** collectors and label series have lifetimes independent of individual HTTP requests. Registry ownership and initial series creation must be explicit.

**Correction/design:** ApplicationMetrics owns a CollectorRegistry per app instance. It initializes known 200/500 request series, the error counter, and the duration histogram before traffic. Only GET / is counted as business traffic.

**Verification:** test_versions_have_independent_registries, test_zero_error_series_exists_before_traffic, and test_exact_counts_and_duration_with_excluded_endpoints cover these behaviors.

**Prevention:** retain factory isolation and these tests when adding endpoints. Do not mix probes and scrape requests into business-error denominators.

**Limitation:** counters reset when processes restart. Prometheus rates require samples over time; initial zero series do not remove that requirement. See the Phase 4 scrape-baseline incident.

## P1-03: Simulated failures and probes

**Classification/status:** intentional behavior and implemented safeguards, not a health-check bug.

**Symptom:** GET / returns HTTP 500 with FAILURE_RATE=100 while /health and /ready can still return 200.

**Cause:** business failure injection is deliberately separate from process liveness and readiness. This keeps an unhealthy release observable for the monitoring and recovery workflow.

**Correction/design:** use /health and /ready for availability and use business metrics for release quality. Invalid failure rates are rejected by configuration validation; 0 and 100 provide deterministic boundaries.

**Verification:** test_release_behavior, test_partial_failure_boundary, test_readiness_tracks_lifecycle, and test_invalid_failure_rate cover the intended distinctions. Unexpected exceptions are separately tested for preserved identity and error counting.

**Prevention:** do not change probe semantics just to make a deliberately bad release appear unhealthy to Kubernetes. Use a deterministic 100-percent failure setting for failure-path tests; a small sample at 30 percent need not contain exactly 30 percent failures.

**Limitation:** Kubernetes process health does not itself implement the business-error rollback policy. Automatic recovery belongs to Phase 6.

## Available validation and missing history

The application guide records prior unit and real-server checks. The Phase 5 execution later ran the full expanded suite successfully. These are historical results; writing this document did not rerun servers or inject faults.

There is no preserved evidence here for specific Phase 1 installation failures, missing-module incidents, or port collisions. The operating guide's troubleshooting table remains useful preventive guidance, but it is not proof those events occurred.
