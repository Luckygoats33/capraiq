#!/usr/bin/env bash
# Usage: ./run.sh [test|demo]   (default: test then demo). Offline; no credentials needed.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}
case "${1:-all}" in
  test) $PY -m pytest -q ;;
  demo) $PY -m capraiq demo --out out ;;
  all)  $PY -m pytest -q && $PY -m capraiq demo --out out ;;
  *) echo "usage: $0 [test|demo]"; exit 2 ;;
esac
