"""Загрузчик линии Фонбета через OddsPapi.

Запускается раз в день. Берёт только турниры, где в ближайшие LOOKAHEAD_H часов
есть наши матчи, чтобы уложиться в бесплатный лимит (250 запросов в месяц).
Пишет data/odds.json: список матчей с компактным списком коэффициентов.
"""
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.oddspapi.io/v4/"
KEY = os.environ.get("ODDSPAPI_KEY", "").strip()
BOOKMAKER = "fonbet"
LOOKAHEAD_H = 40
PAUSE = 1.6  # сервис ограничивает частоту запросов

# Наши коды турниров -> id турниров OddsPapi
TOURNAMENTS = {"pl": 17, "ll": 8, "sa": 23, "bl": 35, "l1": 34, "cl": 7, "el": 679, "efl": 21, "rpl": 203}

# Какие рынки сохраняем (остальное нам пока не нужно)
KEEP_TYPES = {
    "1x2", "doublechance", "drawnobet", "bothteamsscore", "totals",
    "teamtotals-team1", "teamtotals-team2", "toscore-team1", "toscore-team2",
    "totals-corners", "teamtotals-corners-team1", "teamtotals-corners-team2",
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
NOW = datetime.now(timezone.utc)
used = 0


def call(path, **params):
    global used
    params["apiKey"] = KEY
    url = API + path + "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        try:
            used += 1
            with urllib.request.urlopen(req, timeout=60) as r:
                d = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    d = gzip.decompress(d)
                return json.loads(d)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(3)
                continue
            raise
        finally:
            time.sleep(PAUSE)


def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(name, obj):
    tmp = os.path.join(DATA, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(DATA, name))


def main():
    if not KEY:
        print("Нет ключа ODDSPAPI_KEY")
        return 1
    meta = load("meta.json", {})
    horizon = NOW + timedelta(hours=LOOKAHEAD_H)
    need = []
    for code, u in (meta.get("upcoming") or {}).items():
        if code not in TOURNAMENTS:
            continue
        ms = (u.get("matches") or []) + ((u.get("next") or {}).get("matches") or [])
        if any(NOW - timedelta(hours=2) <= datetime.fromisoformat(m["t"].replace("Z", "+00:00")) <= horizon for m in ms):
            need.append(code)
    force = [c for c in os.environ.get("ODDS_TOURNAMENTS", "").split(",") if c in TOURNAMENTS]
    need = sorted(set(need) | set(force))
    print("Турниры с матчами в ближайшие", LOOKAHEAD_H, "ч:", need)

    # Описание рынков: кэшируем, обновляем раз в 30 дней
    markets = load("op_markets.json", {})
    if not markets or markets.get("_ts", "") < (NOW - timedelta(days=30)).isoformat():
        arr = call("markets", sportId=10)
        markets = {"_ts": NOW.isoformat()}
        for m in arr:
            if m["marketType"] in KEEP_TYPES:
                markets[str(m["marketId"])] = [m["marketType"], m["period"], m["handicap"],
                                               {str(o["outcomeId"]): o["outcomeName"] for o in m["outcomes"]}]
        save("op_markets.json", markets)

    names = load("op_teams.json", {})
    out = []
    missing = set()
    for code in need:
        try:
            fixtures = call("odds-by-tournaments", bookmaker=BOOKMAKER, tournamentIds=TOURNAMENTS[code])
        except Exception as e:  # noqa: BLE001
            print(f"{code}: ошибка {type(e).__name__} {e}")
            continue
        n = 0
        for f in fixtures if isinstance(fixtures, list) else []:
            b = (f.get("bookmakerOdds") or {}).get(BOOKMAKER)
            if not b or not b.get("bookmakerIsActive", True):
                continue
            rows = []
            for mid, m in (b.get("markets") or {}).items():
                d = markets.get(mid)
                if not d:
                    continue
                mtype, period, line, oc = d
                for oid, o in (m.get("outcomes") or {}).items():
                    p = ((o.get("players") or {}).get("0") or {})
                    if p.get("price") and p.get("active", True):
                        rows.append([mtype, period, line, oc.get(oid, oid), p["price"]])
            if not rows:
                continue
            p1, p2 = str(f["participant1Id"]), str(f["participant2Id"])
            for pid in (p1, p2):
                if pid not in names:
                    missing.add(pid)
            out.append({"c": code, "t": f["startTime"], "p1": p1, "p2": p2, "odds": rows})
            n += 1
        print(f"{code}: матчей с линией {n}")

    if missing:
        try:
            allp = call("participants", sportId=10)
            for pid in missing:
                if pid in allp:
                    names[pid] = allp[pid]
            save("op_teams.json", names)
        except Exception as e:  # noqa: BLE001
            print("participants: ошибка", type(e).__name__, e)

    for f in out:
        f["n1"], f["n2"] = names.get(f["p1"], ""), names.get(f["p2"], "")

    # Сохраняем, сохраняя линии турниров, которые сегодня не обновляли
    old = load("odds.json", {"fixtures": []})
    keep_old = [f for f in old.get("fixtures", []) if f["c"] not in need
                and datetime.fromisoformat(f["t"].replace("Z", "+00:00")) > NOW - timedelta(hours=3)]
    save("odds.json", {"updated": NOW.isoformat(timespec="seconds"), "bookmaker": BOOKMAKER,
                       "fixtures": keep_old + out})
    print(f"Готово: матчей {len(out)} (+{len(keep_old)} старых), запросов потрачено {used}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
