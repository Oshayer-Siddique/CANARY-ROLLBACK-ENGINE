#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
K3D_BIN="$ROOT_DIR/.tools/bin/k3d"
CLUSTER_NAME=canary-mvp
if [[ ! -x "$K3D_BIN" ]]; then echo "k3d is missing." >&2; exit 1; fi
if ! command -v kubectl >/dev/null; then echo "kubectl is required." >&2; exit 1; fi
if "$K3D_BIN" cluster get "$CLUSTER_NAME" >/dev/null 2>&1; then
  echo "Cluster $CLUSTER_NAME already exists."
else
  "$K3D_BIN" cluster create "$CLUSTER_NAME" --servers 1 --agents 1 \
    --k3s-arg "--disable=traefik@server:*" --wait --timeout 180s
fi
kubectl --context "k3d-$CLUSTER_NAME" get nodes
