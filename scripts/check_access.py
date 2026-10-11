"""Проверка, что бесплатный ключ API-Football даёт нужные нам данные.

Запускается в GitHub Actions. Ключ берётся из секрета API_FOOTBALL_KEY
и нигде не печатается.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

_lines = []
_print = print


def print(*a, **k):  # дублируем вывод в файл, чтобы результат был виден в репозитории
    _lines.append(" ".join(str(x) for x in a))
    _print(*a, **k)

BASE = "https://v3.football.api-sports.io"
KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
SEASON = int(os.environ.get("SEASON", "2026"))
LEAGUES = {"АПЛ": 39, "Ла Лига": 140, "Серия А": 135, "Бундеслига": 78,
           "Лига 1": 61, "Лига чемпионов": 2, "Лига Европы": 3}


def get(path, **params):
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"x-apisports-key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def errors(resp):
    e = resp.get("errors")
    return e if e else None


def main():
    if not KEY:
        print("Секрет API_FOOTBALL_KEY не задан.")
        return 1
    st = get("status")
    if errors(st):
        print("Ключ не принят:", st["errors"])
        return 1
    acc = st["response"]
    print(f"Тариф: {acc['subscription']['plan']}, "
          f"запросов сегодня: {acc['requests']['current']} из {acc['requests']['limit_day']}")

    ok = True
    print(f"\nСезон {SEASON}, последний сыгранный матч в каждом турнире:")
    sample = None
    for name, lid in LEAGUES.items():
        r = get("fixtures", league=lid, season=SEASON, last=1)
        if errors(r):
            ok = False
            print(f"  {name}: ОШИБКА {r['errors']}")
        elif not r["response"]:
            print(f"  {name}: матчей нет")
        else:
            f = r["response"][0]
            sample = sample or f["fixture"]["id"]
            print(f"  {name}: OK ({f['teams']['home']['name']} - {f['teams']['away']['name']})")

    if sample:
        s = get("fixtures/statistics", fixture=sample, half="true")
        if errors(s):
            ok = False
            print("\nСтатистика матча: ОШИБКА", s["errors"])
        else:
            team = s["response"][0] if s["response"] else {}
            names = [x["type"] for x in team.get("statistics", [])]
            halves = "statistics_1h" in team or "statistics_2h" in team
            print("\nСтатистика матча: OK")
            print("  показатели:", ", ".join(names) or "пусто")
            print("  по таймам:", "есть" if halves else "нет")

    print("\nИтог:", "доступ есть" if ok else "текущий сезон недоступен на этом тарифе")
    return 0 if ok else 2


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:  # сетевые ошибки и т.п.
        print("Сбой:", type(e).__name__, e)
        code = 3
    with open("check_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
    sys.exit(code)
