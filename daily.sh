#!/usr/bin/env bash
# Daily refresh: compute trade date -> fetch -> (on success) build -> commit & push.
# Safe to run from Windows Task Scheduler / cron / GitHub Actions.
#
# The fetch script only writes its completion marker when it actually retrieved
# the full universe, so a run that got throttled by Eastmoney never produces a
# wrong treemap -- it just caches whatever pages it got and lets the next
# scheduled run continue.
set -uo pipefail
cd "$(dirname "$0")"
PY="C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe"
# CI runs on Linux where that Windows path does not exist -- fall back to python3.
if ! "$PY" --version >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

LOG_DIR=".logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"

log(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

log "=== daily refresh start ==="

FORCE="${FORCE:-0}"

# 1) authoritative trade date from the index quote (handles holidays / T+1 close)
TRADE_DATE=$("$PY" - <<'PY' 2>>"$LOG"
import json, urllib.request, datetime
try:
    req = urllib.request.Request(
        "https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001&fields=f124",
        headers={"User-Agent": "Mozilla/5.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
    ep = (d.get("data") or {}).get("f124")
    if ep:
        dt = datetime.datetime.fromtimestamp(int(ep)).date()
        print(dt.strftime("%Y-%m-%d") if dt.weekday() < 5 else "")
    else:
        print("")
except Exception:
    print("")
PY
)
if [ -z "$TRADE_DATE" ]; then
  TRADE_DATE=$(date +%F)
  log "交易日探测失败，回退到 $TRADE_DATE"
fi
# Weekend guard (the index probe already excludes weekends; this is a belt-and-braces check)
if [ "$FORCE" != "1" ]; then
  dow=$(date -d "$TRADE_DATE" +%u 2>/dev/null || echo 0)
  if [ "$dow" -ge 6 ]; then log "非交易日 $TRADE_DATE，跳过"; exit 0; fi
fi
log "交易日: $TRADE_DATE"
export SNAPSHOT_DATE="$TRADE_DATE"

# 2) fetch (exits 3 on a non-weekday; 0 otherwise -- even on partial throttling)
FORCE="$FORCE" "$PY" fetch_ashare.py 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
if [ "$rc" = "3" ]; then
  log "非交易日，跳过"
  exit 0
fi

MARKER="data/_pages/.complete_$TRADE_DATE"

if [ -f "$MARKER" ]; then
  # fetch completed this (or a prior) run -> build the snapshot
  if [ -f "data/$TRADE_DATE.json" ] && [ "$FORCE" != "1" ]; then
    log "data/$TRADE_DATE.json 已存在，跳过构建"
  else
    SNAPSHOT_DATE="$TRADE_DATE" "$PY" build.py 2>&1 | tee -a "$LOG"
  fi
  # commit & push if this is a git repo
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
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
else
  # partial / throttled this run: persist any cached pages so the next scheduled
  # run can continue, then bail (no snapshot is built from incomplete data).
  log "本运行抓取不完整（上游限流），下次定时运行会继续"
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if [ -z "$(git config user.email || true)" ]; then
      git config user.name  "github-actions[bot]"
      git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    fi
    git add data/_pages 2>>"$LOG"
    if ! git diff --staged --quiet; then
      git commit -q -m "cache progress for $TRADE_DATE" 2>&1 | tee -a "$LOG"
      if [ "${PUSH:-1}" = "1" ]; then
        if git push 2>&1 | tee -a "$LOG"; then log "已推送缓存"; else log "推送失败（可稍后重试）"; fi
      fi
    fi
  fi
fi

log "=== done ==="
