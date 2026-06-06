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

WEEKDAY="$(date '+%u')" # 1=Monday ... 7=Sunday
case "$WEEKDAY" in
  1)
    SEND_LABEL="Monday primary batch"
    SEND_LIMIT=30
    MIN_QUEUE=1
    ;;
  2|3)
    SEND_LABEL="Tuesday/Wednesday overflow batch"
    SEND_LIMIT=25
    MIN_QUEUE=25
    ;;
  *)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipping morning Gmail send; hybrid schedule only sends Monday plus Tuesday/Wednesday overflow." >> logs/send.log
    exit 0
    ;;
esac

QUEUE_COUNT="$("$PYTHON" - <<'PY'
from config import load_settings
import db

settings = load_settings()
with db.connect(settings.database_path, settings.database_url) as conn:
    db.init_db(conn)
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM send_queue q
        JOIN leads l ON l.id = q.lead_id
        WHERE q.queue_status = 'queued'
          AND l.queue_status = 'queued'
          AND l.status IN ('queued', 'send_ready')
          AND COALESCE(l.email_sent, 0) = 0
          AND COALESCE(l.manually_skipped, 0) = 0
          AND l.email_lower IS NOT NULL
          AND l.email_lower != ''
        """
    ).fetchone()
print(int(row["count"] or 0))
PY
)"

if (( QUEUE_COUNT < MIN_QUEUE )); then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipping $SEND_LABEL; queued=$QUEUE_COUNT, required=$MIN_QUEUE." >> logs/send.log
  exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting $SEND_LABEL Gmail send; queued=$QUEUE_COUNT, limit=$SEND_LIMIT"
"$PYTHON" run_sender.py --live --limit "$SEND_LIMIT" >> logs/send.log 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Finished $SEND_LABEL Gmail send"
