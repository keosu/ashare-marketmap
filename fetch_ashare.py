"""Fetch the full A-share universe (market cap + daily change) from Eastmoney
and write data.json. Used both locally and by the daily GitHub Action."""
import json, os, sys, time, datetime
import urllib.request
from concurrent.futures import ThreadPoolExecutor

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


def fetch(page):
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1&np=1"
           "&fltt=2&invt=2&fid=f20&fs=%s&fields=%s" % (page, FS, FIELDS))
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                j = json.loads(r.read().decode("utf-8", "ignore"))
            d = (j or {}).get("data") or {}
            return d.get("diff") or [], d.get("total", 0)
        except Exception as e:
            if attempt == 3:
                print("fail page", page, e, file=sys.stderr)
                return [], 0
            time.sleep(1.2 * (attempt + 1))
    return [], 0


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
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, (diff, t) in enumerate(ex.map(fetch, range(1, PAGES + 1)), 1):
            rows.extend(diff)
            if t:
                total = t
            if i % 15 == 0:
                print("got %d" % len(rows), file=sys.stderr)
    print("total reported:", total, "fetched:", len(rows), file=sys.stderr)

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

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print("[ok] %d stocks, total mcap %.1f yi" %
          (len(out), sum(x["v"] for x in out) / 1e8), file=sys.stderr)
    print("index tradedate:", fetch_index_tradedate(), file=sys.stderr)


if __name__ == "__main__":
    main()
