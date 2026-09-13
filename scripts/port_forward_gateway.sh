#!/usr/bin/env bash
set -euo pipefail
PORT=${1:-18080}
exec kubectl --context k3d-canary-mvp --namespace canary-mvp port-forward service/canary-gateway-nginx "${PORT}:80"
