#!/usr/bin/env bash
# Daily refresh: fetch -> build -> (optional) commit & push.
# Safe to run from Windows Task Scheduler / cron; skips non-trading days.
set -uo pipefail
cd "$(dirname "$0")"
PY="C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe"
# CI runs on Linux where that Windows path does not exist — fall back to python3.
if ! "$PY" --version >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

LOG_DIR=".logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"

log(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

log "=== daily refresh start ==="

# 1) fetch (script itself skips weekends via exit code 3)
FORCE="${FORCE:-0}" "$PY" fetch_ashare.py 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
if [ "$rc" = "3" ]; then
  log "非交易日，跳过"
  exit 0
fi
if [ "$rc" != "0" ]; then
  log "抓取失败 rc=$rc，保留上一份数据"
  exit "$rc"
fi

# 2) authoritative trade date from the index quote
TRADE_DATE=$("$PY" - <<'PY' 2>>"$LOG"
import json, urllib.request, datetime
req = urllib.request.Request(
    "https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001&fields=f124",
    headers={"User-Agent": "Mozilla/5.0"})
d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
ep = (d.get("data") or {}).get("f124")
print(datetime.datetime.fromtimestamp(int(ep)).strftime("%Y-%m-%d") if ep else "")
PY
)
if [ -z "$TRADE_DATE" ]; then
  TRADE_DATE=$(date +%F)
  log "交易日探测失败，回退到 $TRADE_DATE"
fi
log "交易日: $TRADE_DATE"

# 3) idempotency — nothing to do if today is already committed
if [ -f "data/$TRADE_DATE.json" ] && [ -z "${FORCE:-}" ]; then
  log "data/$TRADE_DATE.json 已存在，跳过"
  exit 0
fi

# 4) build (keep the offline snapshot inlined so the local file works standalone)
SNAPSHOT_DATE="$TRADE_DATE" "$PY" build.py 2>&1 | tee -a "$LOG"

# 5) commit & push if this is a git repo
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  # A fresh CI checkout has no committer identity configured.
  if [ -z "$(git config user.email || true)" ]; then
    git config user.name  "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  fi
  git add -A index.html data/ 2>>"$LOG"
  if git diff --staged --quiet; then
    log "无变化"
  else
    git commit -q -m "snapshot: A-share market cap map for $TRADE_DATE" 2>&1 | tee -a "$LOG"
    if [ "${PUSH:-1}" = "1" ]; then
      if git push 2>&1 | tee -a "$LOG"; then log "已推送"; else log "推送失败（可稍后重试）"; fi
    fi
  fi
fi

log "=== done ==="
