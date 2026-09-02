#!/bin/bash
# UltraBot — Start both Backend and Frontend
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "================================"
echo "  UltraBot — Starting..."
echo "================================"

# Backend directory
BACKEND_DIR="$PROJECT_DIR/ultrabot-web/backend"

# Check Python venv
if [ ! -d "$BACKEND_DIR/venv" ]; then
  echo "[ERROR] Python venv not found at $BACKEND_DIR/venv"
  echo "Run: $PROJECT_DIR/setup.sh"
  exit 1
fi

# Start backend in background
echo "[1/2] Starting FastAPI backend on port 8000..."
cd "$BACKEND_DIR"
export PYTHONPATH="$BACKEND_DIR"
source venv/bin/activate
python -m uvicorn app:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
echo "       Backend PID: $BACKEND_PID"

# Wait for backend health check
echo "       Waiting for backend to start..."
for i in $(seq 1 30); do
  if curl -s http://127.0.0.1:8000/health > /dev/null 2>&1; then
    echo "       Backend is ready!"
    break
  fi
  sleep 1
done

if ! curl -s http://127.0.0.1:8000/health > /dev/null 2>&1; then
  echo "[WARN] Backend health check failed, but continuing anyway..."
fi

# Start frontend
echo "[2/2] Starting Next.js frontend on port 3000..."
cd "$PROJECT_DIR"
npx next dev -p 3000 &

# Wait for both
echo ""
echo "  UltraBot is running:"
echo "    Frontend: http://localhost:3000"
echo "    Backend:  http://localhost:8000"
echo "    Press Ctrl+C to stop both"
echo "================================"

cleanup() {
  echo ""
  echo "Shutting down..."
  kill $BACKEND_PID 2>/dev/null
  wait $BACKEND_PID 2>/dev/null
  exit 0
}

trap cleanup SIGINT SIGTERM
trap "kill $BACKEND_PID 2>/dev/null; wait $BACKEND_PID 2>/dev/null" EXIT

wait
