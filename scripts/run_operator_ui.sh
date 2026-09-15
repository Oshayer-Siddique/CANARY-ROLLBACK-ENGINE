#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

npm --prefix frontend run build
operator_python="python3"
if [[ -x .venv/bin/python ]]; then
  operator_python=".venv/bin/python"
fi
exec "$operator_python" -m uvicorn management.main:app --host 127.0.0.1 --port 8080
