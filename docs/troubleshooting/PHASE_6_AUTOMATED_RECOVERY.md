# Phase 6: Automated recovery implementation history

Operating guide: [AUTOMATED_RECOVERY.md](../AUTOMATED_RECOVERY.md).

## P6-01: Controller build and execution separation

**Classification:** implemented safeguard, not a runtime failure.

The application build context deliberately excludes non-application files. The controller therefore has its own Dockerfile and restricted context under recovery/. It uses direct standard-library API calls rather than assuming kubectl exists inside the application image.

**Verification:** controller container built and imported successfully; Kubernetes reported a ready controller Deployment. Runtime decisions are validated separately by the live verifier.

**Prevention:** preserve independent build contexts and never add Kubernetes tooling or elevated credentials to the demo application to make recovery work.

## P6-02: Formatting errors during initial implementation

**Classification:** observed development issue; corrected.

Initial lint checks reported E501 line-length violations in controller logging and live-verifier lines. Formatting was applied, then targeted lint and unit checks rerun.

**Cause:** initial generated lines exceeded the repository's 100-character setting.

**Correction:** Ruff formatting; no policy behavior was changed by formatting.

**Prevention:** inspect lint output before relying on a later shell exit status, and rerun the check after formatting.

## P6-03: Recovery request versus verified recovery

**Classification:** implemented safeguard; covered by offline state-machine tests.

A successful Kubernetes patch cannot prove Gateway behavior. The controller stores pending recovery state in an HTTPRoute annotation before later checking accepted conditions and twenty fresh stable responses. A restarted controller resumes pending verification.

**Verification:** offline tests cover pending recovery after restart, repeated baseline steps, and failed Gateway verification retaining pending state. These tests do not replace the real traffic exercise.

**Prevention:** preserve pending/verified distinction in future release history and dashboards. Never mark rollback complete solely because a patch request succeeded.

## P6-04: Missing data and competing writers

**Classification:** implemented safeguards, with explicit MVP limitations.

Zero errors are valid only with sufficient traffic and fresh target/rule data. Missing, nonfinite, or inadequate samples are unknown. Prometheus/API failures emit controller_error and retry; they do not count as healthy.

A resourceVersion precondition prevents overwriting a concurrent route change without re-reading it. A new route generation restarts the observation period.

**Verification:** offline tests cover unknown data, patch conflicts, generation changes, and stable unavailability.

**Prevention:** retain explicit zero handling learned in Phase 4, and keep tests for 5-percent boundaries. Add release-specific metric identity before supporting overlapping releases. Multi-controller operation needs leader election.

## Evidence and status

The first complete live exercise passed on September 13, 2026. Healthy v2 stayed at 90/10. The failing v2 reached 100-percent errors; the controller requested recovery at route generation 7 and verified generation 8 with twenty fresh v1 requests. The independent verifier received another 200 successful v1-only responses and exited zero after restoring healthy v2 and stable-only routing.

All 49 offline tests and targeted lint passed. RBAC checks allowed the named route patch and denied Deployment patches. No runtime fault requiring a controller correction was observed in this live run. Outage, conflict, restart, and failed-verification cases were tested offline rather than all being injected live.
