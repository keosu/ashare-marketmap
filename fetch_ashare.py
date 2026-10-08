"""Fetch the A-share universe (market cap + daily change) and write data.json.

Two data sources, tried in order:

1. Eastmoney (primary) -- one large `pz=6000` request. Works great from a normal
   broadband IP (the user's PC) and returns the freshest industry tags. GitHub
   Actions' *shared* IP is throttled hard by Eastmoney, so this usually returns
   nothing there.

2. Tencent (fallback, reliable from anywhere) -- `qt.gtimg.cn` is NOT throttled on
   GitHub's IP. We batch-fetch live price / change% / total-market-cap for every
   code listed in ``industry_map.json`` (a static code -> {name, industry,
   market} table extracted from a past Eastmoney snapshot and committed to the
   repo). Industry comes from that table, so the treemap keeps its sector
   partitioning even though the live numbers come from Tencent.

Only when we actually recovered a (near-)complete universe do we write data.json
and the ``.complete_<date>`` marker. daily.sh only builds / commits a snapshot
when that marker exists, so a throttled / failed run never produces a wrong
treemap.
"""
import json, os, sys, time, datetime
import urllib.request

UA_EAST = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
UA_TENC = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36",
    "Referer": "https://gu.qq.com/",
}
EM_FIELDS = "f12,f14,f2,f3,f20,f21,f100,f13"
EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

PZ = int(os.environ.get("PZ", "6000"))
# Sanity floor: a full universe run yields well over 5000 valid names.
MIN_ROWS = int(os.environ.get("MIN_ROWS", "5000"))
# Minimum fraction of the expected universe we must recover to call it complete.
COMPLETE_RATIO = float(os.environ.get("COMPLETE_RATIO", "0.95"))

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data.json")
PAGES_ROOT = os.path.join(HERE, "data", "_pages")
MAP_PATH = os.path.join(HERE, "industry_map.json")


# ---- helpers -----------------------------------------------------------
def is_weekday(d):
    return d.weekday() < 5


def fetch_index_tradedate():
    url = ("https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001"
           "&fields=f43,f57,f58,f124")
    try:
        req = urllib.request.Request(url, headers=UA_EAST)
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8", "ignore"))
        ep = (j.get("data") or {}).get("f124")
        if ep:
            return datetime.datetime.fromtimestamp(int(ep)).strftime("%Y-%m-%d")
    except Exception as e:
        print("index date probe failed:", e, file=sys.stderr)
    return None


def num(v):
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- source 1: Eastmoney ----------------------------------------------
def em_fetch():
    """One big request. Returns list[dict] on success, else None."""
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=%d&po=1&np=1"
           "&fltt=2&invt=2&fid=f20&fs=%s&fields=%s" % (PZ, EM_FS, EM_FIELDS))
    try:
        req = urllib.request.Request(url, headers=UA_EAST)
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8", "ignore"))
        d = (j or {}).get("data") or {}
        diff = d.get("diff") or []
        total = d.get("total", 0)
    except Exception as e:
        print("eastmoney request failed:", e, file=sys.stderr)
        return None
    if not diff or (total and len(diff) < total * COMPLETE_RATIO) \
            or len(diff) < MIN_ROWS:
        print("eastmoney incomplete (got %d / %s) -- will try Tencent"
              % (len(diff), total or "?"), file=sys.stderr)
        return None
    seen = {}
    for r in diff:
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
    print("eastmoney ok: %d stocks" % len(out), file=sys.stderr)
    return out


# ---- source 2: Tencent (reliable from GitHub CI) -----------------------
def tenc_prefix(code, m):
    # 北交所 codes (8xxxxx / 43xxxx / 47xxxx / 92xxxx) live under the "bj" prefix
    # on Tencent even though Eastmoney sometimes tags them with m=0.
    if code[:1] == "8" or code.startswith(("43", "47", "92")):
        return "bj" + code
    if m == 1:
        return "sh" + code
    if m == 51:
        return "bj" + code
    if m == 0:
        return "sz" + code
    if code.startswith(("60", "68", "9", "5", "11", "113", "110")):
        return "sh" + code
    if code.startswith(("0", "3", "2", "12", "123", "124")):
        return "sz" + code
    return "sh" + code


def tenc_fetch(market_map):
    if not market_map:
        return None
    UA = UA_TENC
    items = []  # (tencent_code, plain_code, meta)
    for code, meta in market_map.items():
        items.append((tenc_prefix(code, meta.get("m")), code, meta))
    prices = {}
    B = 100
    for i in range(0, len(items), B):
        q = ",".join(it[0] for it in items[i:i + B])
        try:
            req = urllib.request.Request("https://qt.gtimg.cn/q=" + q, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                txt = r.read().decode("gbk", "ignore")
        except Exception as e:
            print("tencent batch %d failed: %s" % (i, e), file=sys.stderr)
            continue
        for line in txt.split(";"):
            line = line.strip()
            if not line.startswith("v_"):
                continue
            eq = line.find("=")
            key = line[2:eq]
            s = line[eq + 2:-1] if line.endswith('"') else line[eq + 1:]
            f = s.split("~")
            if len(f) <= 45:
                continue
            price = num(f[3])
            if price is None or price == 0:
                continue
            prices[key] = f
        time.sleep(0.12)
    out = []
    for qt_code, code, meta in items:
        f = prices.get(qt_code)
        if not f:
            continue
        price = num(f[3])
        if price is None:
            continue
        total_yi = num(f[45])   # 总市值, 亿元
        float_yi = num(f[44])   # 流通市值, 亿元
        out.append({
            "c": code,
            "n": f[1] or meta.get("n") or code,
            "p": price,
            "d": num(f[32]),
            "v": (total_yi * 1e8) if total_yi else 0,
            "f": (float_yi * 1e8) if float_yi else 0,
            "i": meta.get("i") or "其他",
            "m": meta.get("m"),
        })
    out = [x for x in out if x["v"] > 0]
    out.sort(key=lambda x: -x["v"])
    got = len(out)
    exp = len(market_map)
    print("tencent got %d / %d" % (got, exp), file=sys.stderr)
    if exp and got < exp * COMPLETE_RATIO:
        return None
    if got < MIN_ROWS:
        return None
    return out


def load_market_map():
    try:
        return json.load(open(MAP_PATH, encoding="utf-8"))
    except Exception:
        return None


# ---- completion marker --------------------------------------------------
def marker_path(date_str):
    return os.path.join(PAGES_ROOT, ".complete_%s" % date_str)


def clear_marker(date_str):
    try:
        os.remove(marker_path(date_str))
    except OSError:
        pass


def ensure_marker_dir():
    os.makedirs(PAGES_ROOT, exist_ok=True)


def main():
    force = os.environ.get("FORCE", "") == "1"
    today = datetime.date.today()
    if not force and not is_weekday(today):
        print("[skip] %s is not a weekday" % today, file=sys.stderr)
        sys.exit(3)

    trade_date = (os.environ.get("SNAPSHOT_DATE")
                  or fetch_index_tradedate()
                  or today.strftime("%Y-%m-%d"))

    out = em_fetch()
    src = "eastmoney"
    if not out:
        market_map = load_market_map()
        if market_map:
            out = tenc_fetch(market_map)
            src = "tencent"
        else:
            print("[fail] no industry_map.json and eastmoney failed", file=sys.stderr)

    if not out:
        clear_marker(trade_date)
        print("[partial] no complete source this run; next run will retry",
              file=sys.stderr)
        sys.exit(0)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    ensure_marker_dir()
    with open(marker_path(trade_date), "w", encoding="utf-8") as f:
        f.write(trade_date)
    print("[ok] src=%s %d stocks, total mcap %.1f yi"
          % (src, len(out), sum(x["v"] for x in out) / 1e8), file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
