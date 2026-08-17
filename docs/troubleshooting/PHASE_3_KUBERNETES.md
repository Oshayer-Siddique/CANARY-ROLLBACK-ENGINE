# Phase 3: Kubernetes infrastructure issues

Operating reference: [Kubernetes guide](../KUBERNETES.md).
Evidence: [deployment script](../../scripts/deploy_kubernetes.sh), [canary Deployment](../../infra/kubernetes/base/canary-deployment.yaml), and recorded cluster events from the Phase 4 investigation.

## P3-01: Local image-pull failures during Phase 4

**Classification/status:** observed infrastructure incident, encountered while validating Phase 4. Mitigated locally; the underlying reason for recurring image unavailability remains unconfirmed.

**Context:** changing v2's failure environment triggers a replacement pod. The verifier waits for the Deployment rollout before generating traffic.

**Symptoms:** rollout status timed out after 180 seconds. Events included ErrImagePull and ImagePullBackOff, with this excerpt:

> failed to resolve reference "docker.io/library/application:v2": pull access denied

**Impact:** replacement v2 could not start, preventing monitoring failure/recovery verification.

**Investigation:** inspected Deployment status, replicas, pod state, and events. Events showed a pull attempt against a local demo tag that is not available from the public registry. Re-importing the local images reported success for both the server and agent nodes. One subsequent run succeeded, but another replacement again encountered an image-pull failure.

**Confirmed cause:** the failing pod's runtime could not use the requested local image and its registry fallback failed. **Not confirmed:** why image availability later differed, whether imports were retained consistently, or whether node storage/runtime behavior contributed. Earlier claims that one specific node was conclusively responsible exceeded the preserved event evidence.

**Correction/workaround:** re-imported existing images using:

```bash
.tools/bin/k3d image import application:v1 application:v2 --cluster canary-mvp
```

The canary manifest was then pinned to k3d-canary-mvp-server-0. Subsequent recorded rollouts and the complete Phase 4 verification succeeded.

**Verification:** the final Phase 4 run passed discovery, failure detection, and recovery; Phase 5 later passed its routing exercise. These results establish the tested local configuration, not a permanent fix for image availability across nodes.

**Prevention:** retain image import in setup; inspect the actual failed pod's assigned node and image-related events before concluding a cause. Proposed improvement: verify image availability per node or introduce an accessible registry with reproducible image references.

**Remaining limitation:** pinning reduces scheduling flexibility and does not guarantee future image availability even on the selected node. It is a local workaround, not a production image-distribution strategy. Investigate storage/runtime behavior if the problem recurs; do not hide it by repeatedly increasing rollout timeouts.

## P3-02: Wrong resource name during diagnosis

**Classification/status:** observed diagnostic mistake; corrected.

**Symptom:** querying deployment canary returned:

> deployments.apps "canary" not found

A selector using app=canary-demo,track=canary also found no matching resources.

**Cause:** guessed names/labels did not match this repository. The actual Deployment is application-v2, with app.kubernetes.io/component=canary.

**Correction:** listed namespace resources and used the manifest-defined names and labels:

```bash
kubectl --context k3d-canary-mvp -n canary-mvp get deployments,services
kubectl --context k3d-canary-mvp -n canary-mvp get pods -l app.kubernetes.io/component=canary -o wide
```

**Verification:** those queries showed the existing ready v2 workload. The earlier NotFound was not evidence that the application had disappeared.

**Prevention:** resolve names from manifests or a read-only listing. Use explicit namespace/context and avoid carrying naming assumptions from another project.

## P3-03: Startup probe connection-refused events

**Classification/status:** observed events; not independently established as the persistent failure cause.

**Symptom:** startup probes briefly reported connection refused while new containers were starting.

**Cause interpretation:** a process may not yet be listening during startup. The manifests allow multiple startup failures before declaring failure. In the examined sequence, ready pods and successful rollouts followed some such events.

**Correction:** no probe-timing change was demonstrated as necessary. For the blocked rollout, image-pull events were the actionable evidence.

**Verification strategy:** inspect current readiness, restart counts, event timestamps, and logs together. A historical warning does not prove the pod remains unhealthy.

**Prevention:** preserve startup allowances and investigate persistent failures separately from transient startup events.

## Available baseline validation

The recorded Phase 5 regression run passed the original Kubernetes verifier: stable Service returned v1, canary Service returned v2, and the Gateway returned v1 at 100/0. That verifier assumes stable-only routing; an active 90/10 route intentionally violates its Gateway assertion.

Other original Phase 3 debugging details are not preserved in the reviewed evidence. Controller installation and port-forward troubleshooting instructions in the operating guide are preventive guidance unless accompanied by an incident record.
