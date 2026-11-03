"""Разовая проверка: какие рынки Фонбета приходят через OddsPapi."""
import json, os, urllib.request, urllib.parse, gzip, collections
OUT = "probe"; os.makedirs(OUT, exist_ok=True)
KEY = os.environ.get("ODDSPAPI_KEY", "").strip()
lines = []
import atexit
atexit.register(lambda: open("probe/odds.md", "w").write("\n".join(lines) + "\n"))
def log(*a):
    s = " ".join(str(x) for x in a); lines.append(s); print(s)
def op(path, **params):
    params["apiKey"] = KEY
    req = urllib.request.Request("https://api.oddspapi.io/v4/" + path + "?" + urllib.parse.urlencode(params), headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = r.read()
        if r.headers.get("Content-Encoding") == "gzip": d = gzip.decompress(d)
        return json.loads(d)
names = {str(m["marketId"]): (m["marketName"], m["handicap"], m["marketType"]) for m in json.load(open(f"{OUT}/markets_all.json"))}
import urllib.error
o = []
for tids in ("7", "17"):
    try:
        part = op("odds-by-tournaments", bookmaker="fonbet", tournamentIds=tids)
        log(f"турнир {tids}: ответ типа {type(part).__name__}, длина {len(part)}")
        if isinstance(part, dict): log("  ключи:", list(part)[:10], str(part)[:300])
        else: o += part
    except urllib.error.HTTPError as e:
        log(f"турнир {tids}: HTTP {e.code} {e.read()[:300]!r}")
    except Exception as e:
        log(f"турнир {tids}: {type(e).__name__} {e}")
log("# Фонбет через OddsPapi"); log("матчей:", len(o))
types = collections.Counter(); per = []
for f in o:
    b = f.get("bookmakerOdds", {}).get("fonbet")
    if not b: continue
    ms = b.get("markets", {})
    per.append((f["tournamentId"], f["startTime"], len(ms)))
    for mid in ms: types[names.get(mid, ("?", 0, "?"))[2]] += 1
log("матчей с линией Фонбета:", len(per))
for t, s, n in per[:30]: log(f"  турнир {t} {s} рынков {n}")
log("типы рынков (сколько линий всего):")
for k, v in types.most_common(60): log(f"  {k}: {v}")
if o:
    f = max(o, key=lambda f: len(f.get("bookmakerOdds", {}).get("fonbet", {}).get("markets", {})))
    keep = {mid: m for mid, m in f["bookmakerOdds"]["fonbet"]["markets"].items() if names.get(mid, ("", 0, ""))[2] in ("totals-corners", "totals-bookings", "teamtotals-corners-team1", "teamtotals-bookings-team1", "totals", "bothteamsscore")}
    json.dump({"fixtureId": f["fixtureId"], "tournamentId": f["tournamentId"], "startTime": f["startTime"], "p1": f["participant1Id"], "p2": f["participant2Id"], "markets": keep}, open(f"{OUT}/fonbet_sample.json", "w"), ensure_ascii=False, indent=1)
open(f"{OUT}/odds.md", "w").write("\n".join(lines) + "\n")
