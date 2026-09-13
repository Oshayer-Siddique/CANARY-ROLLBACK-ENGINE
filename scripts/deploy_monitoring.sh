#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONTEXT=k3d-canary-mvp

if ! kubectl --context "$CONTEXT" get nodes >/dev/null; then
  echo "Cluster context $CONTEXT is unavailable. Run scripts/create_k3d_cluster.sh first." >&2
  exit 1
fi

if ! kubectl --context "$CONTEXT" -n canary-mvp get deployment/application-v1 >/dev/null; then
  echo "Application workloads are unavailable. Run scripts/deploy_kubernetes.sh first." >&2
  exit 1
fi

kubectl --context "$CONTEXT" apply -k "$ROOT_DIR/infra/kubernetes/monitoring"
# ConfigMap mounts do not automatically reload Prometheus rules; recreate it to load changes.
kubectl --context "$CONTEXT" -n monitoring rollout restart deployment/prometheus
kubectl --context "$CONTEXT" -n monitoring rollout status deployment/prometheus --timeout=180s
kubectl --context "$CONTEXT" -n monitoring get deployment,pods,service,pvc
echo "Prometheus is ready. Run scripts/port_forward_prometheus.sh and open http://127.0.0.1:19090."
