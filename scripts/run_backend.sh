#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="${PYTHONPATH:-.}"
export RELATIONSHAPE_BACKEND_DB="${RELATIONSHAPE_BACKEND_DB:-runtime/backend.sqlite3}"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8123}"

exec python -m uvicorn relationshape.backend.app:app --host "$HOST" --port "$PORT"
