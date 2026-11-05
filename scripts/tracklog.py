"""Журнал прогнозов: для калибровки модели и проверки «обыгрываем ли линию закрытия».

data/log/pending.json — все оценённые ставки ближайших матчей. Для каждой хранится
первый кэф, который мы увидели, и последний перед стартом (≈ линия закрытия),
плюс вероятности модели/фактов/рынка на последнем снимке.
После матча запись рассчитывается и дописывается в data/log/settled-YYYY-MM.jsonl.
"""
import json
import os
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "data", "log")
DIST_STAT = {"G": ("g", 0), "G1": ("g", 1), "G2": ("g", 2), "C": ("c", 0), "Y": ("y", 0)}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def update(rows, matches, now, spec):
    os.makedirs(LOG, exist_ok=True)
    path = os.path.join(LOG, "pending.json")
    try:
        with open(path, encoding="utf-8") as f:
            pend = json.load(f)
    except FileNotFoundError:
        pend = {}
    stamp = now.isoformat(timespec="minutes")
    for r in rows:
        if ts(r["t"]) <= now:
            continue
        e = pend.get(r["k"])
        if e is None:
            e = dict(r)
            e["first"] = [stamp, r["odds"]]
        else:
            first = e["first"]
            e.update(r)
            e["first"] = first
        e["last"] = [stamp, r["odds"]]
        e.pop("odds", None)
        pend[r["k"]] = e

    byid = {str(m["id"]): m for m in matches}
    settled, keep = {}, {}
    for k, e in pend.items():
        t = ts(e["t"])
        if t > now - timedelta(hours=2, minutes=30):
            keep[k] = e
            continue
        m = byid.get(str(e["mid"]))
        res = settle(e, m, spec) if m else None
        if res is None:
            if t > now - timedelta(days=5):
                keep[k] = e
            continue
        e["res"] = [round(res[0], 3), round(res[1], 3)]
        settled.setdefault(t.strftime("%Y-%m"), []).append(e)
    for month, items in settled.items():
        with open(os.path.join(LOG, f"settled-{month}.jsonl"), "a", encoding="utf-8") as f:
            for e in items:
                f.write(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(keep, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(path + ".tmp", path)
    print(f"Журнал: в ожидании {len(keep)}, рассчитано сейчас {sum(len(v) for v in settled.values())}")


def settle(e, m, spec):
    s = m.get("s")
    if not s:
        return None
    sp = spec(*e["m"])
    if not sp:
        return None
    _, _, dist, fn, _ = sp
    key, idx = DIST_STAT[dist]
    try:
        i, j = s["h"][key][idx], s["a"][key][idx]
    except (KeyError, IndexError, TypeError):
        return None
    if i is None or j is None:
        return None
    return fn(i, j)
