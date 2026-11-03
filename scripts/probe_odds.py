"""Разовая проверка источников коэффициентов. Пишет краткую сводку в probe/odds.md
и небольшие образцы в probe/*.json. Ключ берётся из секрета и никуда не пишется."""
import json, os, urllib.request, urllib.parse, gzip, traceback
OUT = "probe"; os.makedirs(OUT, exist_ok=True)
KEY = os.environ.get("ODDSPAPI_KEY", "").strip()
lines = []
def log(*a):
    s = " ".join(str(x) for x in a); lines.append(s); print(s)
def get(url, headers=None, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return r.status, (data if raw else json.loads(data))
def op(path, **params):
    params["apiKey"] = KEY
    return get("https://api.oddspapi.io/v4/" + path + "?" + urllib.parse.urlencode(params))[1]
def save(name, obj):
    json.dump(obj, open(f"{OUT}/{name}", "w"), ensure_ascii=False, indent=1)

log("# OddsPapi")
try:
    bms = op("bookmakers")
    names = [b.get("slug") or b.get("bookmaker") or b.get("name") for b in (bms if isinstance(bms, list) else bms.get("data", []))]
    log("букмекеров:", len(names))
    log("похожие на Фонбет/Пари:", [n for n in names if n and any(k in str(n).lower() for k in ("fon", "pari", "bet365", "pinnacle", "1xbet", "winline", "betboom", "marathon"))])
    save("bookmakers_sample.json", (bms if isinstance(bms, list) else bms.get("data", []))[:5])
except Exception as e:
    log("bookmakers: ошибка", type(e).__name__, e)
try:
    t = op("tournaments", sportId=10)
    want = ("premier league", "laliga", "serie a", "bundesliga", "ligue 1", "champions league", "europa league", "efl cup", "premier league russia")
    sel = [x for x in t if any(w in (x.get("tournamentName", "") + " " + x.get("categoryName", "")).lower() for w in want)]
    for x in sel[:40]:
        log(f"  {x.get('tournamentId')} | {x.get('categoryName')} | {x.get('tournamentName')} | upcoming {x.get('upcomingFixtures')}")
except Exception as e:
    log("tournaments: ошибка", type(e).__name__, e)
try:
    mk = op("markets", sportId=10)
    arr = mk if isinstance(mk, list) else mk.get("data", [])
    log("рынков:", len(arr))
    save("markets_all.json", arr)
except Exception as e:
    log("markets: ошибка", type(e).__name__, e)
try:
    o = op("odds-by-tournaments", bookmaker="pinnacle", tournamentIds="17")
    log("odds-by-tournaments EPL pinnacle: матчей", len(o))
    if o:
        f = o[0]; mk = f["bookmakerOdds"].get("pinnacle", {}).get("markets", {})
        log("  рынков в первом матче:", len(mk))
        save("odds_epl_pinnacle_first.json", f)
except Exception as e:
    log("odds: ошибка", type(e).__name__, e); log(traceback.format_exc(limit=1))

log("\n# Фонбет (открытая линия)")
for host in ("line01.bkfon-resources.com", "line02.bkfon-resources.com", "line51w.bkfon-resources.com", "line-lb51-w.bkfon-resources.com", "line55w.bkfon-resources.com"):
    url = f"https://{host}/events/list?lang=ru&version=0&scopeMarket=1600"
    try:
        st, data = get(url, raw=True)
        log(f"  {host}: HTTP {st}, {len(data)} байт")
        try:
            j = json.loads(data); log("   ключи:", list(j)[:12])
            ev = j.get("events", []); log("   событий:", len(ev), "коэффициентов-блоков:", len(j.get("customFactors", [])))
            save("fonbet_head.json", {"sports": j.get("sports", [])[:40], "events": ev[:15], "customFactors": j.get("customFactors", [])[:5]})
            break
        except Exception as e:
            log("   не JSON:", data[:120])
    except Exception as e:
        log(f"  {host}: {type(e).__name__} {e}")
open(f"{OUT}/odds.md", "w").write("\n".join(lines) + "\n")
