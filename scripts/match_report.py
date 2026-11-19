"""Справка по матчу для описания ставки.

Запуск (в папке, где лежат meta.json, matches.json, value.json или data/…):
    python match_report.py "Inter" "Como"
    python match_report.py "Inter" "Como" "угловые"     # только ставки с этим словом
Названия — английские, как на сайте (можно часть: "Inter", "Como").
"""
import json
import os
import sys


def load(name):
    for p in (name, os.path.join("data", name)):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    sys.exit(f"Не найден {name}")


def find(T, q):
    q = q.lower()
    exact = [k for k, v in T.items() if v["name"].lower() == q]
    part = [k for k, v in T.items() if q in v["name"].lower()]
    if exact or part:
        return (exact or part)[0]
    sys.exit(f"Команда не найдена: {q}")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    meta, db, val = load("meta.json"), load("matches.json")["matches"], load("value.json")
    T = meta["teams"]
    h, a = find(T, sys.argv[1]), find(T, sys.argv[2])
    word = sys.argv[3].lower() if len(sys.argv) > 3 else ""
    print(f"== {T[h]['name']} – {T[a]['name']}   (линия от {val.get('oddsUpdated')})")
    bets = [b for b in val["bets"] if b["h"] == h and b["a"] == a and word in b["label"].lower()]
    bets.sort(key=lambda b: -b["ev"])
    print("Ставка | кэф | итог.шанс | модель / факты / рынок | справ. | перевес")
    for b in bets[:40]:
        pct = lambda x: "—" if x is None else f"{x*100:.0f}%"
        print(f"{b['label']} | {b['odds']} | {pct(b['p'])} | {pct(b['pm'])} / {pct(b['pf'])} / {pct(b.get('pk'))}"
              f" | {b['fair']} | {b['ev']*100:+.0f}%{'  (возврат ' + pct(b['push']) + ')' if b['push'] > 0.005 else ''}")
    for tid in (h, a):
        print(f"\n-- {T[tid]['name']}: последние матчи (свои–чужие)")
        print("дата | соперник | Д/Г | голы | 1т | 2т | угл | ЖК")
        rows = []
        for m in sorted(db, key=lambda m: m["t"]):
            if tid not in (m["h"], m["a"]) or not m.get("s"):
                continue
            home = m["h"] == tid
            o, p = m["s"]["h" if home else "a"], m["s"]["a" if home else "h"]
            g = lambda s, k, i: "-" if not s.get(k) or s[k][i] is None else s[k][i]
            opp = T.get(m["a"] if home else m["h"], {}).get("name", "?")
            rows.append(f"{m['t'][:10]} | {opp} | {'Д' if home else 'Г'} | {g(o,'g',0)}-{g(p,'g',0)} | "
                        f"{g(o,'g',1)}-{g(p,'g',1)} | {g(o,'g',2)}-{g(p,'g',2)} | {g(o,'c',0)}-{g(p,'c',0)} | "
                        f"{g(o,'y',0)}-{g(p,'y',0)}")
        print("\n".join(rows[-12:]))


if __name__ == "__main__":
    main()
