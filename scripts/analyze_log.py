"""Калибровка: насколько точны модель, факты и рынок, и зарабатывают ли «перевесы».

Запуск: python analyze_log.py data/log/settled-*.jsonl   (можно несколько файлов)
Поля строки журнала: res=[доля выигрыша, доля возврата], first/last=[время, кэф],
pm — модель, pmo — старая модель, pf — факты, pk — рынок без маржи, p — итог сайта,
push — шанс возврата, kind — вид ставки, odds берём last (≈ линия закрытия).
"""
import glob
import json
import sys
from collections import defaultdict

files = [f for a in sys.argv[1:] for f in glob.glob(a)] or glob.glob("data/log/settled-*.jsonl")
rows = [json.loads(l) for f in files for l in open(f, encoding="utf-8")]
rows = [r for r in rows if r["res"][1] < 0.99]
print(f"Ставок: {len(rows)}, матчей: {len({r['mid'] for r in rows})}, файлов: {len(files)}\n")


def brier(rs, key):
    s = n = 0
    for r in rs:
        q = r.get(key)
        if q is None:
            continue
        q = min(max(q / (1 - r["push"]), 1e-3), 1 - 1e-3)
        s += (q - r["res"][0]) ** 2
        n += 1
    return s / n if n else None


keys = ["pk", "p", "pm", "pmo", "pf"]
names = {"pk": "рынок", "p": "итог сайта", "pm": "модель", "pmo": "старая", "pf": "факты"}
print("Brier (меньше = точнее):")
print("вид".ljust(10), *[names[k].ljust(11) for k in keys])
groups = defaultdict(list)
for r in rows:
    groups[r["kind"]].append(r)
for kind, rs in [("ВСЕ", rows)] + sorted(groups.items()):
    vals = [brier(rs, k) for k in keys]
    print(f"{kind:<10}", *[(f"{v:.4f}" if v else "—").ljust(11) for v in vals], len(rs))

print("\nЛучший вес рынка в смеси рынок/(модель+факты):")
for kind, rs in [("ВСЕ", rows)] + sorted(groups.items()):
    rs = [r for r in rs if r.get("pk") is not None and r.get("pm") is not None]
    best = None
    for w10 in range(0, 11):
        w = w10 / 10
        s = 0
        for r in rs:
            own = (r["pm"] + (r["pf"] if r["pf"] is not None else r["pm"])) / 2
            q = min(max((w * r["pk"] + (1 - w) * own) / (1 - r["push"]), 1e-3), 1 - 1e-3)
            s += (q - r["res"][0]) ** 2
        s /= max(len(rs), 1)
        if best is None or s < best[0]:
            best = (s, w)
    print(f"{kind:<10} MARKET_W ≈ {best[1]:.1f}")


def roi(rs):
    if not rs:
        return "—"
    prof = sum(r["res"][0] * r["last"][1] + r["res"][1] - 1 for r in rs)
    clv = sum(r["first"][1] / r["last"][1] - 1 for r in rs) / len(rs)
    return f"n={len(rs):4d}  ROI {prof/len(rs)*100:+6.1f}%  кэф первый/закрытие {clv*100:+5.1f}% (плюс = линия за нас)"


ev = lambda r: r["p"] * r["last"][1] + r["push"] - 1
print("\nСтавки с перевесом, кэф 1.6–2.2:")
for lo, hi in ((-1, 0), (0, 0.05), (0.05, 0.1), (0.1, 0.2), (0.2, 9)):
    sel = [r for r in rows if lo <= ev(r) < hi and 1.6 <= r["last"][1] <= 2.2]
    print(f"перевес {lo:+.2f}…{hi:+.2f}: {roi(sel)}")
print("\nПеревес ≥5% по видам ставок:")
for kind, rs in sorted(groups.items()):
    sel = [r for r in rs if ev(r) >= 0.05 and 1.6 <= r["last"][1] <= 2.2]
    print(f"{kind:<10} {roi(sel)}")
print("\nПеревес ≥5% и модель/факты согласны (|pm−pf| ≤ 0.12):")
sel = [r for r in rows if ev(r) >= 0.05 and 1.6 <= r["last"][1] <= 2.2 and r["pf"] is not None and abs(r["pm"] - r["pf"]) <= 0.12]
print(roi(sel))
