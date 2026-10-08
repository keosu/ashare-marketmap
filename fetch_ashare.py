"""Fetch the full A-share universe (market cap + daily change) from Eastmoney
and write data.json. Used both locally and by the daily GitHub Action."""
import json, os, sys, time, datetime
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
FIELDS = "f12,f14,f2,f3,f20,f21,f100,f13"
FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
PAGES = 60
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data.json")

# ---- trade-day helpers -------------------------------------------------
# Only Mon-Fri; the real exchange holiday list is not available offline, so the
# workflow also compares the payload date against the last trading day reported
# by the index quote and skips the commit when nothing new arrived.
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


def fetch(page, tries=3):
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1&np=1"
           "&fltt=2&invt=2&fid=f20&fs=%s&fields=%s" % (page, FS, FIELDS))
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                j = json.loads(r.read().decode("utf-8", "ignore"))
            d = (j or {}).get("data") or {}
            diff = d.get("diff") or []
            total = d.get("total", 0)
            # The endpoint rate-limits by returning an empty `diff` rather than
            # an error, so an empty page is treated as retryable.
            if diff:
                return diff, total
            last = "empty diff (rate-limited?)"
        except Exception as e:
            last = e
        # back off before the next attempt
        time.sleep(1.2 * (attempt + 1))
    return [], total


def num(v):
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    force = os.environ.get("FORCE", "") == "1"
    today = datetime.date.today()

    if not force and not is_weekday(today):
        print("[skip] %s is not a weekday" % today, file=sys.stderr)
        sys.exit(3)

    rows, total = [], 0
    failed = []
    # Sequential with a small gap between pages. Bursting (even 4 threads) trips
    # the endpoint's rate limiter, which silently returns zero rows and looks
    # like an empty market. One daily run at ~0.3s/page is plenty fast (≈40s).
    for page in range(1, PAGES + 1):
        diff, t = fetch(page)
        rows.extend(diff)
        if t:
            total = t
        elif not diff:
            failed.append(page)
        if page % 15 == 0:
            print("got %d (failed pages: %d)" % (len(rows), len(failed)), file=sys.stderr)
        time.sleep(0.3)
    print("total reported:", total, "fetched:", len(rows), file=sys.stderr)

    # Second pass: retry only the pages that came back empty, sequentially so
    # we stay under the rate limit. Bounded so a hard rate-limit can't stretch
    # the run into a hang — the completeness gate below decides whether we
    # accept the result.
    if failed:
        print("retrying %d failed pages: %s" % (len(failed), failed), file=sys.stderr)
        recovered = 0
        budget = min(len(failed), 24)
        for page in failed[:budget]:
            diff, t = fetch(page)
            if diff:
                rows.extend(diff); recovered += 1
            if t:
                total = t
            time.sleep(1.0)
        print("recovered %d/%d attempted -> %d rows"
              % (recovered, budget, len(rows)), file=sys.stderr)

    # Completeness gate: the endpoint reports ~5900 listings. A partial fetch
    # (rate limiting, network blips) would silently produce a wrong treemap, so
    # refuse to write anything unless we got essentially all of them.
    if total and len(rows) < total * 0.97:
        print("[fail] incomplete fetch: %d/%d rows, keeping previous snapshot"
              % (len(rows), total), file=sys.stderr)
        sys.exit(1)
    if not rows:
        print("[fail] empty dataset, keeping previous", file=sys.stderr)
        sys.exit(1)

    seen = {}
    for r in rows:
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

    if not out:
        print("[fail] empty dataset, keeping previous", file=sys.stderr)
        sys.exit(1)

    # Sanity floor: a normal full-universe run yields >5000 names.
    if len(out) < 5000:
        print("[fail] only %d valid rows, keeping previous snapshot" % len(out),
              file=sys.stderr)
        sys.exit(1)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print("[ok] %d stocks, total mcap %.1f yi" %
          (len(out), sum(x["v"] for x in out) / 1e8), file=sys.stderr)
    print("index tradedate:", fetch_index_tradedate(), file=sys.stderr)


if __name__ == "__main__":
    main()
