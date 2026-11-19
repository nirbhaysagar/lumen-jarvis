#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$ROOT/.venv/bin/python3" ]; then
    PYTHON_EXE="$ROOT/.venv/bin/python3"
else
    PYTHON_EXE="python3"
fi

cd "$ROOT/apps/lumen-core"
exec "$PYTHON_EXE" -m src.main "$@"
