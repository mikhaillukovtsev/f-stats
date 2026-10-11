"""Проверка: отдают ли SofaScore и FotMob данные на серверы GitHub.

Делает несколько запросов и записывает краткий итог в probe_result.txt.
"""
import json
import sys
import traceback

from curl_cffi import requests as cr

OUT = []


def log(*a):
    s = " ".join(str(x) for x in a)
    OUT.append(s)
    print(s)


SOFA_HOSTS = ["https://www.sofascore.com/api/v1", "https://api.sofascore.com/api/v1"]
TOURNAMENTS = {"АПЛ": 17, "Ла Лига": 8, "Серия А": 23, "Бундеслига": 35,
               "Лига 1": 34, "Лига чемпионов": 7, "Лига Европы": 679}


def fetch(url, mode):
    if mode == "plain":
        return cr.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    return cr.get(url, timeout=30, impersonate="chrome")


def probe_sofa():
    log("=== SofaScore ===")
    host = mode = None
    for h in SOFA_HOSTS:
        for m in ("plain", "chrome"):
            try:
                r = fetch(f"{h}/unique-tournament/17/seasons", m)
                log(f"  {h.split('//')[1].split('/')[0]} [{m}]: HTTP {r.status_code}")
                if r.status_code == 200 and not host:
                    host, mode = h, m
            except Exception as e:
                log(f"  {h} [{m}]: сбой {type(e).__name__}")
    if not host:
        log("  Итог: SofaScore недоступен")
        return
    log(f"  Используем {host} [{mode}]")
    sample = None
    for name, tid in TOURNAMENTS.items():
        try:
            seasons = fetch(f"{host}/unique-tournament/{tid}/seasons", mode).json()["seasons"]
            s = seasons[0]
            ev = fetch(f"{host}/unique-tournament/{tid}/season/{s['id']}/events/last/0", mode)
            events = ev.json().get("events", []) if ev.status_code == 200 else []
            done = [e for e in events if e.get("status", {}).get("type") == "finished"]
            last = done[-1] if done else None
            if last and name == "АПЛ":
                sample = last
            log(f"  {name}: сезон {s.get('year')}, сыграно в выборке {len(done)}" +
                (f", последний {last['homeTeam']['name']} - {last['awayTeam']['name']}" if last else ""))
        except Exception as e:
            log(f"  {name}: сбой {type(e).__name__} {e}")
    if sample:
        eid = sample["id"]
        st = fetch(f"{host}/event/{eid}/statistics", mode)
        log(f"  Статистика матча: HTTP {st.status_code}")
        if st.status_code == 200:
            for p in st.json().get("statistics", []):
                names = [i["name"] for g in p["groups"] for i in g["statisticsItems"]]
                want = [n for n in names if any(k in n.lower() for k in
                        ("corner", "yellow", "expected", "on target", "fouls", "offside"))]
                log(f"    период {p['period']}: {', '.join(want)}")
        inc = fetch(f"{host}/event/{eid}/incidents", mode)
        log(f"  События матча (голы, карточки с минутами): HTTP {inc.status_code}")


def probe_fotmob():
    log("=== FotMob ===")
    paths = ["/api/data/leagues?id=47", "/api/data/leagues?id=47&ccode3=NLD",
             "/api/leagues?id=47&ccode3=NLD", "/api/data/matches?date=20261004"]
    ok = None
    for p in paths:
        for m in ("plain", "chrome"):
            try:
                r = fetch("https://www.fotmob.com" + p, m)
                log(f"  {p} [{m}]: HTTP {r.status_code}, {len(r.content)} байт")
                if r.status_code == 200 and "leagues" in p and not ok:
                    ok = (p, m, r)
            except Exception as e:
                log(f"  {p} [{m}]: сбой {type(e).__name__}")
    if not ok:
        return
    p, m, r = ok
    d = r.json()
    log("  ключи ответа:", ", ".join(list(d.keys())[:15]))
    mid = None
    try:
        allm = d["fixtures"]["allMatches"]
        done = [x for x in allm if x.get("status", {}).get("finished")]
        mid = done[-1]["id"] if done else None
        log(f"  матчей в сезоне: {len(allm)}, сыграно: {len(done)}")
    except Exception as e:
        log("  не нашёл список матчей:", type(e).__name__, e)
    if mid:
        for mp in (f"/api/data/matchDetails?matchId={mid}", f"/api/matchDetails?matchId={mid}"):
            r2 = fetch("https://www.fotmob.com" + mp, m)
            log(f"  {mp.split('?')[0]} [{m}]: HTTP {r2.status_code}, {len(r2.content)} байт")
            if r2.status_code == 200:
                try:
                    stats = r2.json()["content"]["stats"]["Periods"]
                    for per, v in stats.items():
                        names = [i["title"] for g in v["stats"] for i in g.get("stats", [])]
                        want = [n for n in names if any(k in n.lower() for k in
                                ("corner", "yellow", "expected", "on target", "fouls", "offside"))]
                        log(f"    период {per}: {', '.join(want)}")
                except Exception as e:
                    log("    статистика не разобрана:", type(e).__name__, e)
                break


if __name__ == "__main__":
    for f in (probe_fotmob,):
        try:
            f()
        except Exception:
            log(traceback.format_exc(limit=2))
    with open("probe_result.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(OUT) + "\n")
