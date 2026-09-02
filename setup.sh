#!/bin/bash
# UltraBot backend — one-time environment setup.
# Creates the Python venv and installs all backend dependencies, including
# the two-step Fyers SDK install (see requirements-fyers.txt for why).
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/ultrabot-web/backend"

echo "================================"
echo "  UltraBot backend — setup"
echo "================================"

cd "$BACKEND_DIR"

if [ ! -d "venv" ]; then
  echo "[1/4] Creating Python venv..."
  python3 -m venv venv
else
  echo "[1/4] venv already exists, reusing it"
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo "[2/4] Installing core backend requirements..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "[3/4] Installing Fyers SDK (--no-deps) and extra dependencies..."
pip install --no-deps -r requirements-fyers.txt -q
pip install -r requirements-fyers-extra.txt -q

echo "[4/4] Verifying imports..."
python -c "import fyers_apiv3; import app" && echo "       Backend imports OK"

echo ""
echo "Setup complete. Run ./start.sh to launch UltraBot."
