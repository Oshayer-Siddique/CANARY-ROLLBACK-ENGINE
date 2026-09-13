#!/usr/bin/env bash
set -euo pipefail

PORT=${1:-19090}
kubectl --context k3d-canary-mvp --namespace monitoring port-forward service/prometheus "$PORT:9090"
