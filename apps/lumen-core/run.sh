#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"

if [ -f "$ROOT/.venv/bin/python3" ]; then
    PYTHON_EXE="$ROOT/.venv/bin/python3"
else
    PYTHON_EXE="python3"
fi

cd "$DIR"
exec "$PYTHON_EXE" -m src.main "$@"
