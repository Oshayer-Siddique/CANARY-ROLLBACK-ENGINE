# Kubernetes deployment guide

Related: [Phase 3 infrastructure issue history](troubleshooting/PHASE_3_KUBERNETES.md).

This document describes the Kubernetes part of the Canary Rollback Engine MVP: the local cluster, workload topology, traffic routing, verification, and day-to-day commands.

## 1. What Phase 3 provides

Phase 3 runs two versions of the FastAPI application in Kubernetes and gives them separate, testable traffic paths:

- **Stable:** three replicas of `application:v1`.
- **Canary:** one replica of `application:v2`.
- **Stable Service:** selects only stable pods.
- **Canary Service:** selects only canary pods.
- **Gateway API route:** sends 100% of external traffic to stable initially.
- **Local cluster:** k3d (k3s in Docker), with one server and one worker node.

The route is deliberately stable-only at the end of Phase 3. A later traffic-shifting phase will change the HTTPRoute weights to introduce 10% canary traffic, and the monitoring/recovery phases will use those signals for automated rollback.

## 2. Architecture and request flow

```text
browser/curl
    |
    v
Gateway canary-gateway (Gateway API, nginx class)
    |
HTTPRoute application-route (100% stable in Phase 3)
    |
Service application-stable:80
    |
three application-v1 pods:8000

Service application-canary:80
    |
one application-v2 pod:8000
```

The canary Service is not selected by the current route, but it is available for direct validation. This isolation lets us prove that v2 is healthy (or intentionally failing) before exposing it to users.

## 3. Repository layout

| Path | Purpose |
| --- | --- |
| `infra/kubernetes/base/namespace.yaml` | Creates the `canary-mvp` namespace. |
| `infra/kubernetes/base/stable-deployment.yaml` | Three-replica v1 Deployment. |
| `infra/kubernetes/base/canary-deployment.yaml` | One-replica v2 Deployment. |
| `infra/kubernetes/base/services.yaml` | Stable and canary ClusterIP Services. |
| `infra/kubernetes/base/kustomization.yaml` | Groups the base resources. |
| `infra/kubernetes/gateway/gateway.yaml` | nginx Gateway API listener. |
| `infra/kubernetes/gateway/httproute.yaml` | Stable-first HTTP routing rule. |
| `scripts/create_k3d_cluster.sh` | Creates or reuses the local k3d cluster. |
| `scripts/deploy_kubernetes.sh` | Imports images and installs/deploys the complete stack. |
| `scripts/port_forward_gateway.sh` | Forwards the generated Gateway data-plane Service. |
| `scripts/verify_kubernetes.py` | Smoke-tests isolation, versions, and 100% stable routing. |

## 4. Prerequisites

Install or make available:

- Docker Engine with permission to run containers.
- `kubectl`.
- `curl`.
- Python 3.12+ for the verification script.

The repository keeps k3d in `.tools/bin/k3d` so the setup does not require a system-wide install. The scripts use that binary automatically.

## 5. Create the local cluster

From the repository root:

```bash
./scripts/create_k3d_cluster.sh
```

The script creates a cluster named `canary-mvp` with one server and one agent and disables bundled Traefik. Disabling Traefik avoids competing ingress controllers because this MVP uses NGINX Gateway Fabric. Running the script again is safe: an existing cluster is reused.

Check the context and nodes:

```bash
kubectl config current-context
kubectl --context k3d-canary-mvp get nodes -o wide
```

Expected context: `k3d-canary-mvp`. Every node should be `Ready`.

## 6. Deploy the application and Gateway API

Run:

```bash
./scripts/deploy_kubernetes.sh
```

The deployment script performs these actions in order:

1. Ensures the k3d cluster exists.
2. Imports local Docker images `application:v1` and `application:v2` into k3d.
3. Applies the namespace, Deployments, and Services with Kustomize.
4. Waits for both application rollouts.
5. Installs the pinned Gateway API standard resources (`v1.6.1`).
6. Installs the pinned NGINX Gateway Fabric release (`v2.7.0`) and waits for its controller.
7. Applies the Gateway and HTTPRoute.
8. Waits until the Gateway is programmed and the route is accepted with resolved backend references.
9. Prints the resulting pods, Services, Gateway, and HTTPRoute.

The pinned versions make a repeatable local setup. Change them in the script only as an intentional dependency upgrade, then rerun deployment and verification.

## 7. Workload configuration

Both Deployments listen on container port 8000 and use the same hardened container settings as the Docker image:

- UID/GID `10001`, `runAsNonRoot: true`.
- No privilege escalation; all Linux capabilities dropped.
- Runtime-default seccomp profile.
- Read-only root filesystem.
- CPU request/limit `50m`/`250m`.
- Memory request/limit `64Mi`/`128Mi`.
- Fifteen-second termination grace period.

Environment variables identify each workload:

| Deployment | `APP_VERSION` | `FAILURE_RATE` |
| --- | --- | --- |
| `application-v1` | `v1` | `0` |
| `application-v2` | `v2` | `0` |

`INSTANCE_ID` comes from the pod name, so responses and logs identify the exact replica.

### Probes

- **Startup probe:** `/health`, every two seconds, allowing up to 30 failures.
- **Liveness probe:** `/health`, every ten seconds, three failures before restart.
- **Readiness probe:** `/ready`, every five seconds, three failures before removal from Service endpoints.

Business request failures do not make `/health` fail. This prevents an intentionally unhealthy canary from being confused with a dead process; the monitoring phase will evaluate `/metrics` and request error rates.

## 8. Service isolation

`application-stable` selects `component=application, version=v1` and `application-canary` selects `component=application, version=v2`. Because the selectors include version, a v1 request cannot accidentally reach v2 and vice versa.

Services are internal `ClusterIP` services. They are normally reached through Gateway API or through a temporary `kubectl port-forward` for local testing.

## 9. Gateway and HTTPRoute

`canary-gateway` uses `gatewayClassName: nginx` and listens for HTTP on port 80. `application-route` attaches to that listener and currently contains one backend reference:

```yaml
backendRefs:
- name: application-stable
  port: 80
  weight: 100
```

NGINX Gateway Fabric creates a data-plane Service named `canary-gateway-nginx`. This generated name is expected; it is the Service that receives the Gateway traffic. The controller Service is separate and should not be used for application requests.

Inspect status:

```bash
kubectl -n canary-mvp get gateway,httproute
kubectl -n canary-mvp describe gateway canary-gateway
kubectl -n canary-mvp describe httproute application-route
```

The Gateway should report `Programmed=True`. The route should report `Accepted=True` and `ResolvedRefs=True`.

## 10. Access the application locally

Start a foreground port-forward:

```bash
./scripts/port_forward_gateway.sh 18080
```

In another terminal:

```bash
curl http://127.0.0.1:18080/
curl http://127.0.0.1:18080/health
curl http://127.0.0.1:18080/metrics
```

Open `http://127.0.0.1:18080/demo` in a browser to view the small demo page. Stop the forwarding process with `Ctrl+C`; it does not modify the cluster.

For direct version checks, use separate temporary forwards:

```bash
kubectl -n canary-mvp port-forward service/application-stable 18081:80
kubectl -n canary-mvp port-forward service/application-canary 18082:80
```

Then query ports 18081 and 18082. The first must return `v1`; the second must return `v2`.

## 11. Verification and acceptance checks

Run the automated smoke test:

```bash
python3 scripts/verify_kubernetes.py
```

It checks that:

- stable has exactly three ready replicas;
- canary has exactly one ready replica;
- stable Service returns v1 for repeated requests;
- canary Service returns v2 for repeated requests;
- the Gateway returns v1 for repeated requests;
- forwarding processes are shut down after the test.

Useful manual checks:

```bash
kubectl -n canary-mvp get deploy,pods,svc
kubectl -n canary-mvp get endpointslice
kubectl -n canary-mvp get events --sort-by=.lastTimestamp
```

To confirm self-healing at the workload level, delete a stable pod and watch Kubernetes replace it:

```bash
kubectl -n canary-mvp delete pod -l version=v1 --wait=false
kubectl -n canary-mvp rollout status deployment/application-v1
kubectl -n canary-mvp get pods -l version=v1
```

This proves replica replacement, not yet application-level automatic rollback. Rollback automation belongs to the monitoring and recovery phases.

## 12. Common operations

```bash
# Follow application logs
kubectl -n canary-mvp logs -l version=v1 --tail=100 -f
kubectl -n canary-mvp logs -l version=v2 --tail=100 -f

# Restart a deployment
kubectl -n canary-mvp rollout restart deployment/application-v1

# Show the rendered manifests
kubectl kustomize infra/kubernetes/base

# Show current route configuration
kubectl -n canary-mvp get httproute application-route -o yaml
```

To test a failing canary without editing the manifest, temporarily set its environment value and restart it:

```bash
kubectl -n canary-mvp set env deployment/application-v2 FAILURE_RATE=30
kubectl -n canary-mvp rollout status deployment/application-v2
```

This change is live-cluster state only. Re-running `deploy_kubernetes.sh` restores the declarative manifest value (`0`).

## 13. Troubleshooting

**No nodes or wrong context:** run `kubectl config use-context k3d-canary-mvp` and rerun `create_k3d_cluster.sh`.

**Pods stuck in `ImagePullBackOff`:** rebuild/import the images, then redeploy:

```bash
docker build --tag application:v1 .
docker build --build-arg APP_VERSION=v2 --tag application:v2 .
./scripts/deploy_kubernetes.sh
```

**Gateway is not programmed:** inspect the controller and conditions:

```bash
kubectl -n nginx-gateway get pods
kubectl -n nginx-gateway logs deployment/nginx-gateway-nginx
kubectl -n canary-mvp describe gateway canary-gateway
```

**Route is not accepted:** verify that the Gateway listener allows routes from `canary-mvp` and that `application-stable` exists on port 80.

**Port-forward says address already in use:** choose another local port, for example `./scripts/port_forward_gateway.sh 19080`, or stop the old forwarding process. Port-forwarding is local to the terminal and is not a Kubernetes failure.

## 14. Cleanup and reset

Stop any port-forward processes, then remove the local cluster when it is no longer needed:

```bash
k3d cluster delete canary-mvp
```

The Docker images and source files remain. The next `create_k3d_cluster.sh` followed by `deploy_kubernetes.sh` recreates the complete environment.

## 15. Phase 3 status and next phases

Phase 3 is complete when the cluster is Ready, both Deployments are rolled out, Gateway/HTTPRoute conditions are healthy, and `scripts/verify_kubernetes.py` passes. The current implementation meets those checks.

Subsequent MVP work:

1. Phase 4 monitoring is complete: see [MONITORING.md](MONITORING.md).
2. Phase 5 weighted routing is complete: see [CANARY_TRAFFIC.md](CANARY_TRAFFIC.md).
3. Phase 6 will add automatic rollback when the canary breaches the health threshold.
4. Phase 7 will verify the integrated release and automatic recovery workflow.

The baseline HTTPRoute now includes stable at weight 100 and canary at weight 0. Reapplying it resets active traffic splitting. Run `python3 scripts/set_canary_traffic.py --canary-percent 0` before this guide's stable-only verifier; an active 90/10 split deliberately violates that verifier's v1-only expectation.

Those features should build on the stable/canary labels, isolated Services, health/readiness probes, and Gateway route established here.
