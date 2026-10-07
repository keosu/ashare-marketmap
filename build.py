"""Build a static site: inline the latest snapshot into index.html and write
per-date JSON files under data/ for the date switcher."""
import json, io, os, datetime, glob

base = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(base, "data"), exist_ok=True)

tpl = io.open(os.path.join(base, "template.html"), encoding="utf-8").read()
data = json.load(io.open(os.path.join(base, "data.json"), encoding="utf-8"))

# ---------- per-date JSON payloads ----------
def norm_date(s):
    s = (s or "").strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s

today = datetime.date.today()
date_str = norm_date(os.environ.get("SNAPSHOT_DATE", "")) or today.isoformat()
ts = os.environ.get("SNAPSHOT_TS") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def to_rows(items):
    rows = []
    for r in items:
        p, d = r["p"], r["d"]
        rows.append([
            r["c"], r["n"],
            None if p is None else round(p, 2),
            None if d is None else round(d, 2),
            int(r["v"]), int(r["f"] or 0), r["i"], r["m"],
        ])
    return rows


rows = to_rows(data)
payload = {
    "date": date_str, "ts": ts, "count": len(rows),
    "mcap": sum(r[4] for r in rows),
    "chg": None, "rows": rows,
}
ds = [r[3] for r in rows if r[3] is not None]
payload["chg"] = round(ds and sum(ds) / len(ds) or 0, 2)

# 1) write today's payload
with io.open(os.path.join(base, "data", f"{date_str}.json"), "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

# 2) merge into manifest (keep any older date files already committed)
entries = []
for path in glob.glob(os.path.join(base, "data", "????-??-??.json")):
    try:
        j = json.load(io.open(path, encoding="utf-8"))
    except Exception:
        continue
    if j.get("date") and j.get("rows"):
        entries.append({"date": j["date"], "count": j.get("count", len(j["rows"])),
                        "chg": j.get("chg", 0), "ts": j.get("ts", "")})
entries.sort(key=lambda e: e["date"], reverse=True)
manifest = {
    "latest": entries[0]["date"] if entries else date_str,
    "updated": ts,
    "dates": entries,
}
with io.open(os.path.join(base, "data", "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=1)

# 3) inline the snapshot as the offline fallback.
#    EMBED=1 keeps index.html self-contained (needed for file:// preview);
#    the daily CI build skips it because the page fetches data/*.json anyway,
#    which keeps each commit small.
inline = os.environ.get("EMBED", "1") != "0"
if inline:
    blob = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    html = tpl.replace("/*__DATA__*/[]", blob).replace("__TS__", ts)
else:
    html = tpl.replace("/*__DATA__*/[]", "[]").replace("__TS__", date_str + " 00:00")

with io.open(os.path.join(base, "index.html"), "w", encoding="utf-8") as f:
    f.write(html)

kb = lambda s: round(len(s.encode("utf-8")) / 1024, 1)
print(f"[build] date={date_str} rows={len(rows)} inline={inline} index={kb(html)}KB "
      f"payload={kb(json.dumps(payload))}KB manifest_dates={len(entries)}")
