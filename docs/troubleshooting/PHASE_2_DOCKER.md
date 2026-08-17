# Phase 2: Containerization issues and safeguards

Operating reference: [Docker guide](../DOCKER.md).
Evidence: [Dockerfile](../../Dockerfile), [.dockerignore](../../.dockerignore), [.gitignore](../../.gitignore), [container verifier](../../scripts/verify_docker.py), and repository commits dc731cb and ee41598.

## P2-01: Environment file tracking

**Classification/status:** repository-history correction around the application/containerization boundary; current exclusion is implemented.

**Symptom/evidence:** commit dc731cb is titled remove .env, but its actual diff only adds .env.example to .gitignore. Commit ee41598, titled fix env, adds the .env** ignore pattern. Both diffs were inspected; environment contents were not copied into this documentation. The current tracked-file listing still includes .env.example.

**Cause:** environment-file tracking required explicit repository exclusions. Available evidence establishes the corrective history, not whether any sensitive values were exposed.

**Correction actually established:** expanded ignore patterns, not a demonstrated tracked-file deletion. Already-tracked .env.example remains tracked despite the ignore rule. The Docker build context separately uses an allowlist and environment-file exclusions, preventing unrelated local files from being copied into the build.

**Verification strategy:** inspect filenames and exclusion rules rather than printing environment values. To investigate later, use git status --short and git check-ignore -v on the specific file. Review the build context separately from Git tracking.

**Prevention:** keep operational values outside tracked source, review filenames before committing, and retain Docker build-context restrictions. If an actual credential exposure is discovered, treat rotation and historical remediation as separate work; no such remediation was performed or established here.

**Limitation:** ignoring a file does not untrack it or erase earlier commits. These records do not claim a file was removed or repository history was purged. Whether the tracked example should remain a public template is a separate configuration decision; no change was made for this documentation task.

## P2-02: Image identity and packaging

**Classification/status:** implemented design safeguards; no original image-identity failure log was preserved.

**Potential symptom:** a container tagged v2 returns v1, or an installed container cannot serve its demo template.

**Cause addressed:** a Docker tag does not automatically set application configuration. Installed packages also need their non-Python assets declared explicitly.

**Correction/design:** the Dockerfile accepts APP_VERSION as a build argument and sets the runtime environment and image label. The package includes app/templates/*.html. FAILURE_RATE remains a runtime setting, allowing the same image to represent healthy or failing behavior.

**Verification:** the Docker guide records the verifier checking identity, template availability, healthy/failing behavior, metrics, non-root execution, health checks, and shutdown. This documentation did not rebuild images or rerun container tests.

**Prevention:** keep build argument, tag, and intended runtime version aligned. Check the actual HTTP response identity, not merely docker images output. Preserve package-data checks when adding assets.

**Limitation:** correct host Docker images do not establish that every Kubernetes node has those images. The observed node image-pull incident is recorded in Phase 3.

## P2-03: Healthy container despite business failures

**Classification/status:** expected behavior; operational interpretation risk.

**Symptom:** the container can be Docker-healthy while business requests intentionally return HTTP 500.

**Cause:** the health check calls /ready. It measures whether the service can respond, not whether its business error percentage meets rollout policy.

**Correction strategy:** inspect business responses and metrics for release quality. Do not treat Docker's health status as an automatic rollback mechanism.

**Verification:** the recorded container verifier includes a fully failing release while checking readiness and health. The Docker guide explains the boundary.

**Prevention:** retain separate availability and business-quality checks in downstream automation.

## Other failures not established as incidents

Socket permission errors, occupied local ports, registry-download failures, and invalid runtime settings appear as possible troubleshooting cases in the Docker guide. No preserved incident output reviewed here establishes which occurred during Phase 2. Use those instructions when symptoms arise, but do not describe them as historical fixes.

The current runtime uses a non-root user and a single worker, while build dependencies live in a separate stage. These are existing implementation choices, not evidence that a security or multiprocess incident occurred.
