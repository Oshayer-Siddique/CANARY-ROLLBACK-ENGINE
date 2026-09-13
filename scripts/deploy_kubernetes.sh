#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
K3D_BIN="$ROOT_DIR/.tools/bin/k3d"
CONTEXT=k3d-canary-mvp
NGF_VERSION=v2.7.0
GATEWAY_API_VERSION=v1.6.1
if [[ ! -x "$K3D_BIN" ]]; then echo "k3d is missing." >&2; exit 1; fi
if ! kubectl --context "$CONTEXT" get nodes >/dev/null; then
  echo "Cluster context $CONTEXT is unavailable. Run scripts/create_k3d_cluster.sh." >&2
  exit 1
fi
"$K3D_BIN" image import application:v1 application:v2 --cluster canary-mvp
kubectl --context "$CONTEXT" apply -k "$ROOT_DIR/infra/kubernetes/base"
kubectl --context "$CONTEXT" -n canary-mvp rollout status deployment/application-v1 --timeout=180s
kubectl --context "$CONTEXT" -n canary-mvp rollout status deployment/application-v2 --timeout=180s
kubectl --context "$CONTEXT" apply -f "https://github.com/kubernetes-sigs/gateway-api/releases/download/$GATEWAY_API_VERSION/standard-install.yaml"
kubectl --context "$CONTEXT" apply --server-side -f "https://raw.githubusercontent.com/nginx/nginx-gateway-fabric/$NGF_VERSION/deploy/crds.yaml"
kubectl --context "$CONTEXT" apply -f "https://raw.githubusercontent.com/nginx/nginx-gateway-fabric/$NGF_VERSION/deploy/default/deploy.yaml"
kubectl --context "$CONTEXT" -n nginx-gateway rollout status deployment/nginx-gateway --timeout=180s
kubectl --context "$CONTEXT" apply -f "$ROOT_DIR/infra/kubernetes/gateway/gateway.yaml"
kubectl --context "$CONTEXT" apply -f "$ROOT_DIR/infra/kubernetes/gateway/httproute.yaml"
kubectl --context "$CONTEXT" -n canary-mvp wait --for=condition=Accepted gateway/canary-gateway --timeout=180s
kubectl --context "$CONTEXT" -n canary-mvp wait --for=condition=Accepted httproute/application-route --timeout=180s
kubectl --context "$CONTEXT" -n canary-mvp wait --for=condition=Programmed gateway/canary-gateway --timeout=180s
kubectl --context "$CONTEXT" -n canary-mvp get deployments,services,gateway,httproute
