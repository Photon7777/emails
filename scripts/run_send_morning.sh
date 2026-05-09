#!/bin/zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

mkdir -p logs data

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting morning Gmail send"
"$PYTHON" run_sender.py --live >> logs/send.log 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Finished morning Gmail send"
