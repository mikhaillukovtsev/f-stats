"""Поиск ставок с перевесом.

Берёт линию Фонбета из data/odds.json, сопоставляет матчи с нашими и для каждой
ставки считает вероятность двумя способами:
  * модель: ожидаемые голы/угловые по истории команд (дом/выезд), распределения;
  * факты: как часто такая ставка заходила в матчах обеих команд за 3 месяца.
Итоговая оценка — среднее. Перевес (ev) = ожидаемая выплата при этом коэффициенте − 1.
Пишет data/value.json.
"""
import difflib
import json
import math
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
NOW = datetime.now(timezone.utc)
MIN_MATCHES = 5          # меньше матчей в истории — команду не считаем
AGREE = 0.12             # насколько модель и факты могут расходиться
VENUE_W = 0.6            # вес домашних/выездных матчей в средних


def load(name, default=None):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------- сопоставление названий ----------

STOP = {"fc", "cf", "ac", "as", "sc", "afc", "ssc", "us", "rc", "cd", "ud", "sd", "rcd", "vfb", "vfl",
        "tsg", "fsv", "sv", "bc", "club", "de", "di", "calcio", "1", "04", "05", "1899", "1909", "1846", "the"}


def norm(name):
    s = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(w for w in s.split() if w not in STOP)


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb)
    return max(jac, difflib.SequenceMatcher(None, a, b).ratio())


# ---------- модель ----------

def nb_pmf(mu, k, disp):
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    if disp <= 1:
        return math.exp(-mu + k * math.log(mu) - math.lgamma(k + 1))
    r = mu / (disp - 1)
    p = r / (r + mu)
    return math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1 - p))


def joint(a, b, disp=1.0, n=30):
    pa = [nb_pmf(a, i, disp) for i in range(n)]
    pb = [nb_pmf(b, j, disp) for j in range(n)]
    return [(i, j, pa[i] * pb[j]) for i in range(n) for j in range(n)]


class Teams:
    def __init__(self, matches):
        self.m = [x for x in matches if x.get("s") and "g" in x["s"]["h"]]

    def side(self, team, where="*"):
        out = []
        for x in self.m:
            if team not in (x["h"], x["a"]):
                continue
            home = x["h"] == team
            if where == "H" and not home or where == "A" and home:
                continue
            out.append((x["s"]["h"], x["s"]["a"]) if home else (x["s"]["a"], x["s"]["h"]))
        return out


def avg(rows, f):
    vals = [f(o, p) for o, p in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def val(d, k, i=0):
    v = d.get(k)
    return v[i] if v else None


def model_for(T, h, a):
    ha, aa, hh, av = T.side(h), T.side(a), T.side(h, "H"), T.side(a, "A")
    if len(ha) < MIN_MATCHES or len(aa) < MIN_MATCHES:
        return None
    lam = {}
    for k in ("g", "xg", "c"):
        def w(r_v, r_all, f):
            x, y = avg(r_v, f), avg(r_all, f)
            if x is None:
                return y
            if y is None:
                return x
            return VENUE_W * x + (1 - VENUE_W) * y
        hf = w(hh, ha, lambda o, p: val(o, k)); hag = w(hh, ha, lambda o, p: val(p, k))
        af = w(av, aa, lambda o, p: val(o, k)); aag = w(av, aa, lambda o, p: val(p, k))
        if None in (hf, hag, af, aag):
            lam[k] = None
        else:
            lam[k] = ((hf + aag) / 2, (af + hag) / 2)
    g = lam["g"]
    if lam["xg"]:
        g = ((g[0] + lam["xg"][0]) / 2, (g[1] + lam["xg"][1]) / 2)
    h1 = tot = 0
    for r in (ha, aa):
        for o, p in r:
            if o["g"][1] is not None and p["g"][1] is not None:
                h1 += o["g"][1] + p["g"][1]; tot += o["g"][0] + p["g"][0]
    share = min(max(h1 / tot, 0.35), 0.55) if tot else 0.45
    return {
        "rows": (ha, aa),
        "G": joint(*g), "G1": joint(g[0] * share, g[1] * share), "G2": joint(g[0] * (1 - share), g[1] * (1 - share)),
        "C": joint(*lam["c"], disp=1.4) if lam["c"] else None,
        "lam": {"g": [round(g[0], 2), round(g[1], 2)], "c": [round(x, 2) for x in lam["c"]] if lam["c"] else None},
    }


# ---------- выплата по ставке ----------

def payout_line(x, line, over):
    """Доля выплаты (win, push) для значения x по линии line. Учитывает .25/.75."""
    if abs(line * 4 - round(line * 4)) < 1e-9 and abs(line * 2 - round(line * 2)) > 1e-9:
        a = payout_line(x, line - 0.25, over); b = payout_line(x, line + 0.25, over)
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    if abs(x - line) < 1e-9:
        return (0.0, 1.0)
    win = x > line if over else x < line
    return (1.0, 0.0) if win else (0.0, 0.0)


def spec(mtype, period, line, outcome):
    """Возвращает (метка, вид, функция по счёту (i,j) -> (win,push), какой дистрибуции, функция факта)."""
    per = {"fulltime": 0, "p1": 1, "p2": 2}.get(period)
    if per is None:
        return None
    pl = {0: "", 1: "1-й тайм ", 2: "2-й тайм "}[per]
    dist = {0: "G", 1: "G1", 2: "G2"}[per]
    over = outcome == "Over"
    tl = f"{line:g}"
    side = "ТБ" if over else "ТМ"
    if mtype == "totals" and outcome in ("Over", "Under"):
        return (f"{pl}Голы {side} {tl}", "goals" if per == 0 else "halves", dist,
                lambda i, j: payout_line(i + j, line, over),
                ("both", lambda o, p: payout_line(o["g"][per] + p["g"][per], line, over) if o["g"][per] is not None and p["g"][per] is not None else None))
    if per != 0:
        return None
    if mtype in ("teamtotals-team1", "teamtotals-team2") and outcome in ("Over", "Under"):
        t = 0 if mtype.endswith("1") else 1
        return (f"@{t} ИТ{'Б' if over else 'М'} {tl}", "team", "G",
                lambda i, j: payout_line((i, j)[t], line, over),
                ("team", t, lambda o, p: payout_line(o["g"][0], line, over), lambda o, p: payout_line(p["g"][0], line, over)))
    if mtype in ("toscore-team1", "toscore-team2") and outcome in ("Yes", "No"):
        t = 0 if mtype.endswith("1") else 1
        yes = outcome == "Yes"
        return (f"@{t} забьёт: {'да' if yes else 'нет'}", "team", "G",
                lambda i, j: ((1.0, 0.0) if ((i, j)[t] > 0) == yes else (0.0, 0.0)),
                ("team", t, lambda o, p: (1.0, 0.0) if (o["g"][0] > 0) == yes else (0.0, 0.0),
                 lambda o, p: (1.0, 0.0) if (p["g"][0] > 0) == yes else (0.0, 0.0)))
    if mtype == "bothteamsscore" and outcome in ("Yes", "No"):
        yes = outcome == "Yes"
        return (f"Обе забьют: {'да' if yes else 'нет'}", "goals", "G",
                lambda i, j: ((1.0, 0.0) if (i > 0 and j > 0) == yes else (0.0, 0.0)),
                ("both", lambda o, p: (1.0, 0.0) if (o["g"][0] > 0 and p["g"][0] > 0) == yes else (0.0, 0.0)))
    if mtype == "1x2" and outcome in ("1", "X", "2"):
        f = {"1": lambda i, j: i > j, "X": lambda i, j: i == j, "2": lambda i, j: j > i}[outcome]
        return ({"1": "П1", "X": "Ничья", "2": "П2"}[outcome], "result", "G",
                lambda i, j: (1.0, 0.0) if f(i, j) else (0.0, 0.0), None)
    if mtype == "doublechance" and outcome in ("1X", "12", "2X", "X2"):
        f = {"1X": lambda i, j: i >= j, "12": lambda i, j: i != j, "2X": lambda i, j: j >= i, "X2": lambda i, j: j >= i}[outcome]
        return ({"1X": "1X", "12": "12", "2X": "X2", "X2": "X2"}[outcome], "result", "G",
                lambda i, j: (1.0, 0.0) if f(i, j) else (0.0, 0.0), None)
    if mtype == "drawnobet" and outcome in ("1", "2"):
        return ({"1": "Ф1(0)", "2": "Ф2(0)"}[outcome], "result", "G",
                lambda i, j: (0.0, 1.0) if i == j else ((1.0, 0.0) if (i > j) == (outcome == "1") else (0.0, 0.0)), None)
    if mtype == "totals-corners" and outcome in ("Over", "Under"):
        return (f"Угловые {side} {tl}", "corners", "C",
                lambda i, j: payout_line(i + j, line, over),
                ("both", lambda o, p: payout_line(o["c"][0] + p["c"][0], line, over) if val(o, "c") is not None and val(p, "c") is not None else None))
    if mtype in ("teamtotals-corners-team1", "teamtotals-corners-team2") and outcome in ("Over", "Under"):
        t = 0 if mtype.endswith("1") else 1
        return (f"@{t} угловые {side} {tl}", "corners", "C",
                lambda i, j: payout_line((i, j)[t], line, over),
                ("team", t, lambda o, p: payout_line(o["c"][0], line, over) if val(o, "c") is not None else None,
                 lambda o, p: payout_line(p["c"][0], line, over) if val(p, "c") is not None else None))
    return None


def fact(model, fspec):
    ha, aa = model["rows"]

    def mean(rows, f):
        vs = [f(o, p) for o, p in rows]
        vs = [v for v in vs if v is not None]
        if len(vs) < MIN_MATCHES:
            return None
        return (sum(v[0] for v in vs) / len(vs), sum(v[1] for v in vs) / len(vs), len(vs))

    if fspec[0] == "both":
        a = mean(ha, fspec[1]); b = mean(aa, fspec[1])
        if not a or not b:
            return None
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    _, t, own, conceded = fspec
    team_rows, opp_rows = (ha, aa) if t == 0 else (aa, ha)
    a = mean(team_rows, own); b = mean(opp_rows, conceded)
    if not a or not b:
        return None
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def main():
    odds = load("odds.json")
    meta = load("meta.json")
    db = load("matches.json", {"matches": []})
    if not odds or not meta:
        print("Нет данных")
        return 0
    T = Teams(db["matches"])
    names = meta["teams"]
    ours = []
    for code, u in meta["upcoming"].items():
        for blk in (u, u.get("next") or {}):
            for m in blk.get("matches") or []:
                ours.append({**m, "c": code, "round": blk.get("round", "")})

    bets = []
    matched = unmatched = 0
    for f in odds["fixtures"]:
        t = ts(f["t"])
        if t < NOW - timedelta(minutes=5):
            continue
        cands = [m for m in ours if m["c"] == f["c"] and abs((ts(m["t"]) - t).total_seconds()) < 4 * 3600]
        best, score = None, 0
        for m in cands:
            s = sim(names[m["h"]]["name"], f["n1"]) + sim(names[m["a"]]["name"], f["n2"])
            if s > score:
                best, score = m, s
        if not best or score < 1.1:
            unmatched += 1
            print(f"  не сопоставлен: {f['c']} {f['t']} {f['n1']} – {f['n2']}")
            continue
        matched += 1
        model = model_for(T, best["h"], best["a"])
        if not model:
            continue
        team_names = (names[best["h"]]["name"], names[best["a"]]["name"])
        for mtype, period, line, outcome, price in f["odds"]:
            s = spec(mtype, period, line, outcome)
            if not s:
                continue
            label, kind, dist, fn, fspec = s
            D = model.get(dist)
            if D is None:
                continue
            w = sum(p * fn(i, j)[0] for i, j, p in D)
            pu = sum(p * fn(i, j)[1] for i, j, p in D)
            fw = fp = None
            if fspec:
                fr = fact(model, fspec)
                if fr:
                    fw, fp = fr
            if fw is not None:
                bw, bp = (w + fw) / 2, (pu + fp) / 2
                agree = abs(w - fw) <= AGREE
            else:
                bw, bp, agree = w, pu, None
            if bw <= 0.02:
                continue
            ev = bw * price + bp - 1
            fair = (1 - bp) / bw
            if ev < -0.08 or not (1.2 <= price <= 5):
                continue
            label = label.replace("@0", team_names[0]).replace("@1", team_names[1])
            bets.append({
                "mid": best["id"], "c": best["c"], "t": best["t"], "h": best["h"], "a": best["a"],
                "label": label, "kind": kind, "odds": price,
                "p": round(bw, 3), "push": round(bp, 3), "pm": round(w, 3),
                "pf": round(fw, 3) if fw is not None else None,
                "fair": round(fair, 2), "ev": round(ev, 3), "agree": agree,
            })
    bets.sort(key=lambda b: -b["ev"])
    out = {"updated": NOW.isoformat(timespec="seconds"), "oddsUpdated": odds.get("updated"),
           "bookmaker": odds.get("bookmaker"), "bets": bets}
    with open(os.path.join(DATA, "value.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"Сопоставлено матчей {matched}, нет пары {unmatched}, ставок в файле {len(bets)}, "
          f"с перевесом 5%+ и согласием {sum(1 for b in bets if b['ev'] >= 0.05 and b['agree'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
