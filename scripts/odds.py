"""Линия Фонбета (= Пари) напрямую из публичной ленты Фонбета.

Лента одна на весь сайт (~12 МБ), без ключей и лимитов. Берём только матчи наших
турниров и нужные рынки: исходы, тоталы, инд. тоталы, обе забьют — по матчу и
таймам, плюс угловые и жёлтые карточки. Пишет data/odds.json.
"""
import gzip
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

URLS = "https://fon.bet/urls.json"
FALLBACK_LINE = ["//line-lb51.bk6bba-resources.com", "//line-lb52.bk6bba-resources.ru"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
           "Accept-Encoding": "gzip"}
BOOKMAKER = "fonbet"
HORIZON_DAYS = 8

# Сегменты Фонбета (только футбол: parentId == 1) -> наши коды. Отсекаем долгосрочные и спецставки.
SEGMENTS = [
    ("pl", r"^Англия\. Премьер-Лига"),
    ("efl", r"^Англия\. Кубок (лиги|Карабао)"),
    ("ll", r"^Испания\. Примера дивизион"),
    ("sa", r"^Италия\. Серия А(?![0-9])"),
    ("bl", r"^Германия\. Бундеслига(?! ?[0-9])"),
    ("l1", r"^Франция\. Лига 1"),
    ("pt", r"^Португалия\. Премьер-Лига"),
    ("cl", r"^Лига Чемпионов УЕФА"),
    ("el", r"^Лига Европы УЕФА"),
    ("rpl", r"^Россия\. Премьер-Лига"),
    ("nl", r"^Лига [Нн]аций УЕФА"),
]
SKIP_SEG = re.compile(r"Итоги|бомбардир|Специальн|Статист|Кто выше|Лучший|показател", re.I)

# Номера факторов Фонбета (одинаковые для матча, таймов, угловых и карточек).
F_1X2 = {921: "1", 922: "X", 923: "2"}
F_DC = {924: "1X", 925: "2X", 1571: "12"}
F_BTTS = {4241: "Yes", 4242: "No"}
PAIRS_TOTAL = [(930, 931), (1696, 1697), (1727, 1728), (1730, 1731), (1733, 1734), (1736, 1737), (1739, 1791),
               (1793, 1794), (1796, 1797), (1799, 1800), (1802, 1803), (1805, 1806)]
PAIRS_IT1 = [(1809, 1810), (1812, 1813), (1815, 1816), (1818, 1819), (1821, 1822), (1824, 1825), (1827, 1828),
             (1830, 1831)]
PAIRS_IT2 = [(1854, 1871), (1873, 1874), (1880, 1881), (1883, 1884), (1886, 1887), (1893, 1894), (1896, 1897),
             (1899, 1900), (2203, 2204)]
OU = {}
for group, pairs in (("totals", PAIRS_TOTAL), ("teamtotals-team1", PAIRS_IT1), ("teamtotals-team2", PAIRS_IT2)):
    for o, u in pairs:
        OU[o] = (group, "Over")
        OU[u] = (group, "Under")

# Дочерние события: название -> (суффикс рынка, период)
CHILD = {
    "1-й тайм": ("", "p1"),
    "2-й тайм": ("", "p2"),
    "угловые": ("corners", "fulltime"),
    "желтые карты": ("cards", "fulltime"),
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
NOW = datetime.now(timezone.utc)


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=90) as r:
        d = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            d = gzip.decompress(d)
        return json.loads(d)


def load_list():
    try:
        hosts = get(URLS).get("line") or FALLBACK_LINE
    except Exception as e:  # noqa: BLE001
        print("urls.json:", type(e).__name__, e)
        hosts = FALLBACK_LINE
    last = None
    for h in hosts:
        url = ("https:" + h if h.startswith("//") else h) + "/events/list?lang=ru&version=0&scopeMarket=1600"
        try:
            return get(url)
        except Exception as e:  # noqa: BLE001
            last = e
            print("линия", h, type(e).__name__, e)
    raise last


def market_name(base, suffix):
    if not suffix:
        return base
    if base == "totals":
        return f"totals-{suffix}"
    if base.startswith("teamtotals-"):
        return f"teamtotals-{suffix}-{base.split('-')[1]}"
    return f"{base}-{suffix}"


def rows_for(factors, suffix, period):
    out = []
    for f in factors:
        fid, v = f.get("f"), f.get("v")
        if not v or f.get("isLive"):
            continue
        if fid in OU:
            base, oc = OU[fid]
            try:
                line = float(f.get("pt"))
            except (TypeError, ValueError):
                continue
            out.append([market_name(base, suffix), period, line, oc, v])
        elif fid in F_1X2:
            out.append([market_name("1x2", suffix), period, 0.0, F_1X2[fid], v])
        elif fid in F_DC and not suffix:
            out.append(["doublechance", period, 0.0, F_DC[fid], v])
        elif fid in F_BTTS and not suffix:
            out.append(["bothteamsscore", period, 0.0, F_BTTS[fid], v])
    return out


def main():
    j = load_list()
    seg_code = {}
    for s in j.get("sports", []):
        if s.get("kind") != "segment" or s.get("parentId") != 1 or SKIP_SEG.search(s.get("name", "")):
            continue
        for code, rx in SEGMENTS:
            if re.search(rx, s.get("name", "")):
                seg_code[s["id"]] = code
                break
    horizon = NOW + timedelta(days=HORIZON_DAYS)
    roots = {}
    for e in j.get("events", []):
        if e.get("level") != 1 or e.get("kind") != 1 or e.get("place") != "line":
            continue
        if e.get("sportId") not in seg_code or not e.get("team1") or not e.get("team2"):
            continue
        t = datetime.fromtimestamp(e["startTime"], timezone.utc)
        if not (NOW < t < horizon):
            continue
        roots[e["id"]] = {"c": seg_code[e["sportId"]], "t": t.isoformat().replace("+00:00", "Z"),
                          "n1": e["team1"], "n2": e["team2"], "fid": e["id"], "odds": []}
    child = {}
    for e in j.get("events", []):
        if e.get("parentId") in roots and e.get("name") in CHILD:
            child[e["id"]] = (e["parentId"], *CHILD[e["name"]])
    for cf in j.get("customFactors", []):
        eid = cf.get("e")
        if eid in roots:
            roots[eid]["odds"] += rows_for(cf.get("factors") or [], "", "fulltime")
        elif eid in child:
            pid, suffix, period = child[eid]
            roots[pid]["odds"] += rows_for(cf.get("factors") or [], suffix, period)
    fixtures = [f for f in roots.values() if f["odds"]]
    fixtures.sort(key=lambda f: f["t"])
    os.makedirs(DATA, exist_ok=True)
    tmp = os.path.join(DATA, "odds.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"updated": NOW.isoformat(timespec="seconds"), "bookmaker": BOOKMAKER, "fixtures": fixtures},
                  fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(DATA, "odds.json"))
    by = {}
    for f in fixtures:
        by[f["c"]] = by.get(f["c"], 0) + 1
    cards = sum(1 for f in fixtures if any(r[0].endswith("cards") or "-cards-" in r[0] for r in f["odds"]))
    print(f"Фонбет: матчей {len(fixtures)} {by}, с линией на ЖК {cards}, "
          f"коэффициентов {sum(len(f['odds']) for f in fixtures)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
