// Cross-platform launcher for the FastAPI backend dev server.
//
// Why this exists: `PYTHONPATH=. venv/Scripts/python -m uvicorn ...` in
// package.json only works on Unix shells — Windows cmd/PowerShell doesn't
// support `VAR=value command` syntax and fails immediately with exit code 1,
// silently leaving the backend never started while the frontend runs fine.
//
// This script:
//   - picks the correct venv python path for the current OS
//     (venv/Scripts/python.exe on Windows, venv/bin/python elsewhere)
//   - uses uvicorn's --app-dir flag instead of the PYTHONPATH env var,
//     so no shell-specific env-var syntax is needed at all
//   - falls back to system python3/python if venv isn't set up yet, with
//     a clear message pointing at setup.sh

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const backendDir = path.join(__dirname, '..', 'ultrabot-web', 'backend');
const isWindows = process.platform === 'win32';

// Minimal .env loader (no dependency) — Next.js auto-loads .env for its own
// process, but this is a plain `node` script and needs it explicitly.
function loadDotEnv(envPath) {
  if (!fs.existsSync(envPath)) return;
  const lines = fs.readFileSync(envPath, 'utf8').split('\n');
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let value = trimmed.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = value;
  }
}
loadDotEnv(path.join(__dirname, '..', '.env'));

const candidateVenvs = [
  isWindows
    ? path.join(__dirname, '..', 'venv', 'Scripts', 'python.exe')
    : path.join(__dirname, '..', 'venv', 'bin', 'python'),
  isWindows
    ? path.join(backendDir, 'venv', 'Scripts', 'python.exe')
    : path.join(backendDir, 'venv', 'bin', 'python'),
];

let pythonCmd;
const foundVenv = candidateVenvs.find((p) => fs.existsSync(p));
if (foundVenv) {
  pythonCmd = foundVenv;
  console.log('[dev:backend] Using Python virtualenv:', foundVenv);
} else {
  console.error('[dev:backend] venv not found at candidate paths:', candidateVenvs);
  console.error('[dev:backend] Run setup.sh (or setup manually) first — falling back to system python for now.');
  pythonCmd = isWindows ? 'python' : 'python3';
}


// Host/port come from BACKEND_URL (same var next.config.ts reads) so
// there's one place to change if the default port is blocked — e.g.
// Windows sometimes reserves ports like 8000 for Hyper-V/WSL NAT, causing
// WinError 10013. Fix: set BACKEND_URL in .env to an unreserved port,
// e.g. http://127.0.0.1:8001, and both frontend proxy + backend follow it.
const backendUrl = new URL(process.env.BACKEND_URL || 'http://127.0.0.1:8000');
const HOST = backendUrl.hostname;
const PORT = backendUrl.port || '8000';

const args = [
  '-m', 'uvicorn',
  'app:app',
  '--app-dir', backendDir,
  '--host', HOST,
  '--port', String(PORT),
];

console.log(`[dev:backend] ${pythonCmd} ${args.join(' ')}`);

const child = spawn(pythonCmd, args, {
  stdio: 'inherit',
  cwd: backendDir,
  shell: false,
});

child.on('exit', (code) => {
  process.exit(code ?? 1);
});

process.on('SIGINT', () => child.kill('SIGINT'));
process.on('SIGTERM', () => child.kill('SIGTERM'));
