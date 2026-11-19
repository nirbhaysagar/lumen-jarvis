#!/usr/bin/env bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python3"

echo "=========================================="
echo " Starting Lumen Hub Services"
echo "=========================================="

# 1. Ensure Docker Postgres + Redis are healthy
docker compose -f "$PROJECT_ROOT/infra/docker-compose.yml" up -d

# 2. Wait for Postgres to be ready
echo "Checking database readiness..."
until docker exec lumen-postgres pg_isready -U lumen -d lumen_memory > /dev/null 2>&1; do
  sleep 1
done
echo "Postgres + pgvector is ready."

# 3. Start Memory Engine Service in background
echo "Starting Memory Engine API on http://localhost:8000..."
export PYTHONPATH="$PROJECT_ROOT/packages/memory-engine"
"$VENV_PYTHON" -m uvicorn app.main:app --app-dir "$PROJECT_ROOT/packages/memory-engine" --host 127.0.0.1 --port 8000 &
MEMORY_PID=$!

cleanup() {
  echo ""
  echo "Shutting down Memory Engine (PID $MEMORY_PID)..."
  kill "$MEMORY_PID" 2>/dev/null || true
  echo "Standby mode engaged."
}
trap cleanup EXIT INT TERM

# Wait for Memory Engine to respond to health check
echo "Waiting for Memory Engine..."
until curl -s http://127.0.0.1:8000/health > /dev/null 2>&1; do
  sleep 0.5
done
echo "Memory Engine is healthy."

# 4. Launch Lumen Core Interactive CLI
echo "Launching JARVIS Terminal..."
cd "$PROJECT_ROOT/apps/lumen-core"
PYTHONPATH="$PROJECT_ROOT/apps/lumen-core" "$VENV_PYTHON" -m src.main
