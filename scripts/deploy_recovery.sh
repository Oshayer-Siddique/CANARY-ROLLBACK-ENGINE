#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
kubectl --context k3d-canary-mvp -n canary-mvp get httproute application-route >/dev/null
kubectl --context k3d-canary-mvp -n monitoring rollout status deployment/prometheus --timeout=180s
docker build -t recovery-controller:phase6 "$ROOT_DIR/recovery"
"$ROOT_DIR/.tools/bin/k3d" image import recovery-controller:phase6 --cluster canary-mvp
kubectl --context k3d-canary-mvp apply -k "$ROOT_DIR/infra/kubernetes/recovery"
kubectl --context k3d-canary-mvp -n canary-mvp rollout restart deployment/recovery-controller
kubectl --context k3d-canary-mvp -n canary-mvp rollout status deployment/recovery-controller --timeout=180s
