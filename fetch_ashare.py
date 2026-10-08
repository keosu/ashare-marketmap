"""Fetch the A-share universe (market cap + daily change) from Eastmoney and
write data.json.

Design notes (rate-limit strategy)
----------------------------------
Eastmoney throttles requests coming from GitHub Actions' *shared* IP very
aggressively: a calm 12-request pass gets only the first page through, while a
fast 19-request pass squeezed out ~1.9k rows. The pattern is clear -- the fewer
requests we make, the better our odds of getting clean data before the shared
budget is exhausted by everyone else on that IP.

So the primary path is a SINGLE large `pz` request that pulls the whole universe
in one shot (1 request == maximum chance of slipping through). We retry that one
request a few times within the run (small back-off). If the endpoint caps `pz`
and returns a *partial* page (a real total but fewer rows than expected), we fall
back to a handful of large pages and cache each successful page in the repo so
that, on severe throttling, several scheduled runs gradually assemble the full
snapshot (GitHub fires this workflow twice on weekdays plus manual dispatch).

A successful run writes data.json AND a marker file
``data/_pages/.complete_<TRADE_DATE>``.  daily.sh only builds / commits a
snapshot when that marker exists, so an incomplete run (pure throttle, empty
response) never produces a wrong treemap.
"""
import json, os, sys, time, datetime
import urllib.request

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
FIELDS = "f12,f14,f2,f3,f20,f21,f100,f13"
FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

# One big page keeps the request count minimal (GitHub's shared IP throttle is
# request-budget based). Most of the time this returns the whole universe.
PZ = int(os.environ.get("PZ", "6000"))
# Independent retries of that single request within one run.
RETRIES = int(os.environ.get("RETRIES", "4"))
RETRY_GAP = float(os.environ.get("RETRY_GAP", "2.0"))
# Sanity floor: a full universe run yields well over 5000 valid names.
MIN_ROWS = int(os.environ.get("MIN_ROWS", "5000"))
# Minimum fraction of `total` we must recover before we call it complete.
COMPLETE_RATIO = float(os.environ.get("COMPLETE_RATIO", "0.95"))

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data.json")
PAGES_ROOT = os.path.join(HERE, "data", "_pages")


# ---- trade-day helpers -------------------------------------------------
def is_weekday(d):
    return d.weekday() < 5


def fetch_index_tradedate():
    """Ask a major index for its own latest trading date (field f124 = epoch)."""
    url = ("https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001"
           "&fields=f43,f57,f58,f124")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8", "ignore"))
        d = (j.get("data") or {})
        epoch = d.get("f124")
        if epoch:
            return datetime.datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d")
    except Exception as e:
        print("index date probe failed:", e, file=sys.stderr)
    return None


def fetch_page(pn, pz, tries=2, retry_gap=2.0):
    """Return (diff, total). Empty diff means throttled / failed."""
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=%d&po=1&np=1"
           "&fltt=2&invt=2&fid=f20&fs=%s&fields=%s" % (pn, pz, FS, FIELDS))
    total = 0
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                j = json.loads(r.read().decode("utf-8", "ignore"))
            d = (j or {}).get("data") or {}
            diff = d.get("diff") or []
            total = d.get("total", 0)
            if diff:
                return diff, total
        except Exception:
            pass
        time.sleep(retry_gap)
    return [], total


def num(v):
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- per-date page cache (cross-run accumulation) ----------------------
def cache_dir_for(date_str):
    return os.path.join(PAGES_ROOT, date_str)


def load_cache(date_str):
    pages, total = {}, 0
    cd = cache_dir_for(date_str)
    if not os.path.isdir(cd):
        return pages, total
    for fn in sorted(os.listdir(cd)):
        if fn.startswith("page-") and fn.endswith(".json"):
            try:
                j = json.load(open(os.path.join(cd, fn), encoding="utf-8"))
                pages[int(fn[5:-5])] = j.get("diff") or []
                total = j.get("total") or total
            except Exception:
                pass
    return pages, total


def save_page(date_str, pn, diff, total):
    cd = cache_dir_for(date_str)
    os.makedirs(cd, exist_ok=True)
    with open(os.path.join(cd, "page-%d.json" % pn), "w", encoding="utf-8") as f:
        json.dump({"total": total, "diff": diff},
                  f, ensure_ascii=False, separators=(",", ":"))


def marker_path(date_str):
    return os.path.join(PAGES_ROOT, ".complete_%s" % date_str)


def clear_marker(date_str):
    try:
        os.remove(marker_path(date_str))
    except OSError:
        pass


def main():
    force = os.environ.get("FORCE", "") == "1"
    today = datetime.date.today()
    if not force and not is_weekday(today):
        print("[skip] %s is not a weekday" % today, file=sys.stderr)
        sys.exit(3)

    # Trade date: explicit override (daily.sh computes it from the index quote)
    # else probe the index ourselves, else fall back to today.
    trade_date = (os.environ.get("SNAPSHOT_DATE")
                  or fetch_index_tradedate()
                  or today.strftime("%Y-%m-%d"))

    pages, total = load_cache(trade_date)
    done = False

    # --- Phase 1: one big request, retried a few times ----------------
    for i in range(RETRIES):
        diff, t = fetch_page(1, PZ, tries=2, retry_gap=RETRY_GAP)
        if t:
            total = t
        if diff:
            # A clean single request gives the full universe.
            if (not total or len(diff) >= total * COMPLETE_RATIO) \
                    and len(diff) >= MIN_ROWS:
                pages[1] = diff
                save_page(trade_date, 1, diff, total)
                done = True
                break
            # Partial page with a known total => the endpoint capped `pz`.
            # Keep it and switch to the paging fallback below.
            if total and len(diff) < total * COMPLETE_RATIO:
                pages[1] = diff
                save_page(trade_date, 1, diff, total)
        # small back-off so the next attempt lands in a fresher window
        time.sleep(RETRY_GAP * (i + 1))

    # --- Phase 2: paging fallback (only if page 1 returned data) ------
    # Triggered when the endpoint caps `pz` (partial page, real total). This is
    # NOT the throttle case (throttle => empty diff, total 0 => skipped here).
    if not done and total:
        expected = (total + PZ - 1) // PZ
        for pn in range(2, expected + 1):
            if pn in pages:
                continue
            diff, t = fetch_page(pn, PZ, tries=2, retry_gap=RETRY_GAP)
            if t:
                total = t
            if diff:
                pages[pn] = diff
                save_page(trade_date, pn, diff, total)
            got = sum(len(v) for v in pages.values())
            if total and got >= total * COMPLETE_RATIO:
                done = True
                break
            time.sleep(RETRY_GAP)

    got = sum(len(v) for v in pages.values())
    print("trade_date=%s total=%s got=%d done=%s"
          % (trade_date, total, got, done), file=sys.stderr)

    if done and (not total or got >= total * COMPLETE_RATIO):
        # Combine every cached page in order, dedupe, filter, sort.
        seen = {}
        for pn in sorted(pages):
            for r in pages[pn]:
                c = r.get("f12")
                if c:
                    seen[c] = r
        out = []
        for r in seen.values():
            mcap = num(r.get("f20"))
            if not mcap or mcap <= 0:
                continue
            out.append({
                "c": r.get("f12"), "n": r.get("f14"),
                "p": num(r.get("f2")), "d": num(r.get("f3")),
                "v": mcap, "f": num(r.get("f21")) or 0,
                "i": r.get("f100") or "其他", "m": r.get("f13"),
            })
        out.sort(key=lambda x: -x["v"])
        if len(out) < MIN_ROWS:
            print("[fail] only %d valid rows (<%d), keeping previous"
                  % (len(out), MIN_ROWS), file=sys.stderr)
            clear_marker(trade_date)
            try:
                os.remove(OUT)
            except OSError:
                pass
            sys.exit(1)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        # Mark complete; the per-date cache is now redundant -> drop it.
        with open(marker_path(trade_date), "w", encoding="utf-8") as f:
            f.write(trade_date)
        cd = cache_dir_for(trade_date)
        if os.path.isdir(cd):
            import shutil
            shutil.rmtree(cd)
        print("[ok] %d stocks, total mcap %.1f yi"
              % (len(out), sum(x["v"] for x in out) / 1e8), file=sys.stderr)
        sys.exit(0)

    # Incomplete this run: leave any cached pages in place so the next scheduled
    # run can continue. Do NOT write data.json, so daily.sh won't build a
    # partial snapshot. Clear the marker defensively.
    clear_marker(trade_date)
    print("[partial] %d/%s rows cached; next run will continue"
          % (got, total or "?"), file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
