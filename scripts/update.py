"""Загрузчик данных для сайта.

Запускается по расписанию в GitHub Actions:
  1. забирает расписание и таблицы 7 турниров и национальных кубков с FotMob;
  2. для сыгранных матчей, которых ещё нет в базе, скачивает подробную статистику;
  3. удаляет матчи старше 90 дней;
  4. пишет data/matches.json (база сыгранных матчей) и data/meta.json (всё остальное).
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://www.fotmob.com/api/data"
HEADERS = {"User-Agent": "Mozilla/5.0 (f-stats personal project)"}
KEEP_DAYS = 90
SCHEMA = 2                   # версия формата статистики; при росте старые матчи перекачиваются
MAX_DETAILS_PER_RUN = 400   # защита от слишком долгого первого запуска
DELAY = 0.8                  # пауза между запросами, чтобы не нагружать сайт

# Основные турниры: показываются на главной.
MAIN = {
    "pl": (47, "АПЛ"),
    "ll": (87, "Ла Лига"),
    "sa": (55, "Серия А"),
    "bl": (54, "Бундеслига"),
    "l1": (53, "Лига 1"),
    "cl": (42, "Лига чемпионов"),
    "el": (73, "Лига Европы"),
    "efl": (133, "Кубок английской лиги"),
}
# Кубки: только для статистики команд.
CUPS = {
    "fa": (132, "Кубок Англии"),
    "cdr": (138, "Кубок Испании"),
    "cit": (141, "Кубок Италии"),
    "dfb": (209, "Кубок Германии"),
    "cdf": (134, "Кубок Франции"),
}
# Лиги только для статистики: на главной не показываются.
EXTRA = {
    "rpl": (63, "РПЛ"),
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
NOW = datetime.now(timezone.utc)


def get(path, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(API + path, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            print(f"  повтор {path}: {type(e).__name__}")
            time.sleep(3 * (i + 1))
        finally:
            time.sleep(DELAY)


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def num(v):
    """'0.86' -> 0.86, '238 (80%)' -> 238, 5 -> 5, None -> None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(v))
    if not m:
        return None
    x = float(m.group(1))
    return int(x) if x.is_integer() and "." not in m.group(1) else x


def load_json(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(name, obj):
    os.makedirs(DATA, exist_ok=True)
    tmp = os.path.join(DATA, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(DATA, name))


# ---------- разбор статистики матча ----------

STAT_KEYS = {
    "xg": "expected_goals",
    "sot": "ShotsOnTarget",
    "c": "corners",
    "y": "yellow_cards",
    "f": "fouls",
    "o": "Offsides",
    "pos": "BallPossesion",
}


def period_stats(period):
    """Плоский словарь key -> [home, away] по одному периоду."""
    out = {}
    for group in period.get("stats", []):
        for item in group.get("stats", []):
            key, vals = item.get("key"), item.get("stats")
            if item.get("type") == "title" or not key or not isinstance(vals, list) or len(vals) != 2:
                continue
            out.setdefault(key, vals)
    return out


def parse_details(d):
    """Возвращает {'h': {...}, 'a': {...}} или None, если статистики нет.

    Для каждого показателя хранится тройка [матч, 1-й тайм, 2-й тайм].
    Голы считаются за основное время (без дополнительного), как в ставках на 90 минут.
    """
    content = d.get("content") or {}
    events = ((content.get("matchFacts") or {}).get("events") or {}).get("events") or []
    ht = ft = None
    for e in events:
        if e.get("type") == "Half":
            if e.get("halfStrShort") == "HT":
                ht = (e.get("homeScore"), e.get("awayScore"))
            elif e.get("halfStrShort") == "FT":
                ft = (e.get("homeScore"), e.get("awayScore"))
    if ft is None:
        score = (d.get("header") or {}).get("status", {}).get("scoreStr", "")
        m = re.match(r"\s*(\d+)\s*-\s*(\d+)", score)
        if m:
            ft = (int(m.group(1)), int(m.group(2)))

    res = {"h": {}, "a": {}}
    for side, i in (("h", 0), ("a", 1)):
        if ft and ht and None not in ft + ht:
            res[side]["g"] = [ft[i], ht[i], ft[i] - ht[i]]
        elif ft:
            res[side]["g"] = [ft[i], None, None]

    periods = ((content.get("stats") or {}).get("Periods")) or {}
    if periods:
        flat = {p: period_stats(periods.get(p) or {}) for p in ("All", "FirstHalf", "SecondHalf")}
        for short, key in STAT_KEYS.items():
            for side, i in (("h", 0), ("a", 1)):
                trio = [num(flat[p].get(key, [None, None])[i]) for p in ("All", "FirstHalf", "SecondHalf")]
                if any(v is not None for v in trio):
                    res[side][short] = trio
    return res if res["h"] else None


# ---------- определение текущего тура ----------

def rounds_ahead(matches):
    """Тур, который сейчас идёт или будет следующим, и его оставшиеся матчи.

    Тур считается текущим, пока в нём есть несыгранные матчи «по расписанию».
    Перенесённые игры (сыгранные сильно позже основной части тура) не держат тур
    открытым и в список не попадают.
    """
    POSTPONED_GAP = timedelta(days=4)
    by_round = {}
    for m in matches:
        if m["status"].get("cancelled"):
            continue
        by_round.setdefault(m["round"], []).append(m)
    candidates = []
    for rnd, ms in by_round.items():
        times = sorted(parse_time(m["status"]["utcTime"]) for m in ms)
        median = times[len(times) // 2]
        left = [m for m in ms if not m["status"].get("finished")
                and parse_time(m["status"]["utcTime"]) - median <= POSTPONED_GAP]
        if left:
            candidates.append((median, rnd, left))
    candidates.sort(key=lambda x: x[0])
    return [(rnd, sorted(left, key=lambda m: m["status"]["utcTime"])) for _, rnd, left in candidates[:2]]


CUP_ROUNDS = {"1/8": "1/8 финала", "1/4": "1/4 финала", "1/2": "1/2 финала", "final": "Финал"}


def round_label(code, rnd, round_name):
    if rnd is None:
        return ""
    if code in CUPS or code == "efl":
        return f"{rnd}-й раунд" if str(rnd).isdigit() else CUP_ROUNDS.get(str(rnd).lower(), str(round_name or rnd))
    if code in ("cl", "el"):
        return f"{rnd}-й игровой день" if str(rnd).isdigit() else str(round_name or rnd)
    return f"{rnd}-й тур" if str(rnd).isdigit() else str(round_name or rnd)


# ---------- основной процесс ----------

def main():
    db = load_json("matches.json", {"matches": []})
    known = {m["id"]: m for m in db["matches"]}
    cutoff = NOW - timedelta(days=KEEP_DAYS)

    teams = {}       # id -> {name, short}
    main_teams = set()
    standings = {}   # code -> {team_id: [pos, pts, played]}
    upcoming = {}    # code -> {round, matches}
    finished = []    # (code, fixture)

    source_names = {}
    for code, (lid, title) in {**MAIN, **CUPS, **EXTRA}.items():
        print(f"{title}...")
        try:
            d = get(f"/leagues?id={lid}")
        except Exception as e:  # noqa: BLE001
            print(f"  не удалось загрузить: {type(e).__name__} {e}")
            continue
        fixtures = (d.get("fixtures") or {}).get("allMatches") or []
        for m in fixtures:
            for side in ("home", "away"):
                t = m[side]
                teams.setdefault(str(t["id"]), {"name": t["name"], "short": t.get("shortName") or t["name"]})
                if code in MAIN or code in EXTRA:
                    main_teams.add(str(t["id"]))
            st = m["status"]
            if st.get("finished") and not st.get("cancelled") and not st.get("awarded"):
                if parse_time(st["utcTime"]) >= cutoff:
                    finished.append((code, m))

        source_names[code] = (d.get("details") or {}).get("name")
        if code in MAIN or code in EXTRA:
            ahead = rounds_ahead(fixtures)
            pack = lambda rnd, left: {
                "round": round_label(code, rnd, left[0].get("roundName") if left else None),
                "matches": [{
                    "id": m["id"], "t": m["status"]["utcTime"],
                    "h": str(m["home"]["id"]), "a": str(m["away"]["id"]),
                    "live": bool(m["status"].get("started")) and not m["status"].get("finished"),
                } for m in left],
            }
            cur = pack(*ahead[0]) if ahead else {"round": "", "matches": []}
            upcoming[code] = {"title": title, **cur,
                              "next": pack(*ahead[1]) if len(ahead) > 1 else None}
            table = {}
            for block in d.get("table") or []:
                data = block.get("data") or {}
                tables = []
                if isinstance(data.get("table"), dict):
                    tables.append(data["table"].get("all") or [])
                for sub in data.get("tables") or []:   # турниры с группами
                    tables.append(((sub.get("table") or {}).get("all")) or [])
                for rows in tables:
                    for row in rows:
                        table[str(row["id"])] = [row.get("idx"), row.get("pts"), row.get("played")]
            standings[code] = table

    # Сыгранные матчи, где играет хотя бы одна «наша» команда.
    finished = [(c, m) for c, m in finished
                if str(m["home"]["id"]) in main_teams or str(m["away"]["id"]) in main_teams]
    print(f"Сыгранных матчей за {KEEP_DAYS} дней: {len(finished)}, в базе уже: "
          f"{sum(1 for _, m in finished if m['id'] in known and known[m['id']].get('s'))}")

    fetched = failed = 0
    result = []
    for code, m in sorted(finished, key=lambda x: x[1]["status"]["utcTime"], reverse=True):
        rec = known.get(m["id"])
        need = (rec is None
                or (rec.get("s") is None and rec.get("tries", 0) < 3)
                or (rec.get("s") is not None and rec.get("v", 1) < SCHEMA))
        if need and fetched < MAX_DETAILS_PER_RUN:
            try:
                stats = parse_details(get(f"/matchDetails?matchId={m['id']}"))
                fetched += 1
            except Exception as e:  # noqa: BLE001
                print(f"  матч {m['id']}: {type(e).__name__}")
                stats, failed = None, failed + 1
            if stats is None and rec and rec.get("s"):
                result.append(rec)  # не затираем уже собранную статистику
                continue
            rec = {
                "id": m["id"], "c": code, "r": str(m.get("roundName") or m.get("round") or ""),
                "t": m["status"]["utcTime"], "h": str(m["home"]["id"]), "a": str(m["away"]["id"]),
                "s": stats, "tries": (rec or {}).get("tries", 0) + (0 if stats else 1),
                "v": SCHEMA,
            }
            if rec["s"] is None:  # хотя бы счёт из списка матчей
                sc = re.match(r"\s*(\d+)\s*-\s*(\d+)", m["status"].get("scoreStr", ""))
                if sc:
                    rec["score"] = [int(sc.group(1)), int(sc.group(2))]
        if rec:
            result.append(rec)
    print(f"Скачано подробностей: {fetched}, ошибок: {failed}")

    used = {r["h"] for r in result} | {r["a"] for r in result} | main_teams
    for u in upcoming.values():
        for m in u["matches"]:
            used.update((m["h"], m["a"]))
    meta = {
        "updated": NOW.isoformat(timespec="seconds"),
        "comps": {c: t for c, (_, t) in {**MAIN, **CUPS, **EXTRA}.items()},
        "sourceNames": source_names,
        "order": list(MAIN),
        "upcoming": upcoming,
        "standings": standings,
        "teams": {tid: t for tid, t in teams.items() if tid in used},
        "mainTeams": sorted(main_teams),
    }
    result.sort(key=lambda r: r["t"])
    save_json("matches.json", {"matches": result})
    save_json("meta.json", meta)
    print(f"Готово: матчей в базе {len(result)}, команд {len(meta['teams'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
