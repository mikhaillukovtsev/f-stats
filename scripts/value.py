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
MARKET_W = 0.5
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


TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
              ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
               "f", "kh", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
# Русские названия, которые транслитом не угадать
ALIAS = {
    "бавария": "bayern munchen", "псж": "paris saint germain", "байер": "bayer leverkusen",
    "боруссия д": "borussia dortmund", "боруссия м": "borussia monchengladbach", "кельн": "koln",
    "айнтрахт ф": "eintracht frankfurt", "гамбург": "hamburger sv", "лион": "lyon", "марсель": "marseille",
    "ницца": "nice", "ренн": "rennes", "нант": "nantes", "тулуза": "toulouse", "осер": "auxerre",
    "анже": "angers", "гавр": "le havre", "лорьян": "lorient", "ланс": "lens", "лилль": "lille",
    "интер": "inter", "ювентус": "juventus", "наполи": "napoli", "рома": "roma", "лацио": "lazio",
    "атлетико": "atletico madrid", "атлетик": "athletic club", "реал": "real madrid", "бетис": "real betis",
    "сельта": "celta vigo", "сосьедад": "real sociedad", "вильярреал": "villarreal", "севилья": "sevilla",
    "валенсия": "valencia", "хетафе": "getafe", "жирона": "girona", "эспаньол": "espanyol",
    "тоттенхэм": "tottenham hotspur", "вулверхэмптон": "wolverhampton wanderers", "вест хэм": "west ham united",
    "ньюкасл": "newcastle united", "брайтон": "brighton hove albion", "лидс": "leeds united",
    "фенербахче": "fenerbahce", "галатасарай": "galatasaray", "бешикташ": "besiktas", "псв": "psv eindhoven",
    "брюгге": "club brugge", "спортинг": "sporting cp", "бенфика": "benfica", "порту": "porto",
    "ференцварош": "ferencvaros", "црвена звезда": "crvena zvezda", "янг бойз": "young boys",
    "штутгарт": "vfb stuttgart", "фрайбург": "freiburg", "хоффенхайм": "hoffenheim", "майнц": "mainz",
    "унион берлин": "union berlin", "вердер": "werder bremen", "санкт паули": "st pauli",
}


def ru2lat(name):
    s = name.lower().replace("ё", "е").replace("-", " ").replace(".", "")
    s = re.sub(r"\s+", " ", s).strip()
    if s in ALIAS:
        return ALIAS[s]
    for k, v in ALIAS.items():
        if s.startswith(k + " ") or s == k:
            s = v + s[len(k):]
            break
    return "".join(TR.get(ch, ch) for ch in s)


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


def model_old(T, h, a):
    ha, aa, hh, av = T.side(h), T.side(a), T.side(h, "H"), T.side(a, "A")
    if len(ha) < MIN_MATCHES or len(aa) < MIN_MATCHES:
        return None
    lam = {}
    for k in ("g", "xg", "c", "y"):
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
        "Y": joint(*lam["y"], disp=1.3, n=15) if lam["y"] else None,
        "ly": lam["y"],
        "lam": {"g": [round(g[0], 2), round(g[1], 2)], "c": [round(x, 2) for x in lam["c"]] if lam["c"] else None},
    }


# ---------- модель силы команд (атака/оборона с поправкой на соперника) ----------

HALF_LIFE = 45      # дней: матч полуторамесячной давности весит вдвое меньше свежего
PRIOR_K = 4         # «виртуальных» средних матчей, тянущих рейтинг к 1 при малой выборке
DISP = {"g": 1.0, "c": 1.4, "y": 1.3}
REF_K = 10          # сколько матчей судьи нужно, чтобы доверять его средним наполовину


def goal_val(side):
    g, xg = val(side, "g"), val(side, "xg")
    if g is None:
        return None
    return (g + xg) / 2 if xg is not None else g


# Команды вне наших лиг и еврокубков (соперники по кубкам, внутренним лигам PSV, Ференцвароша…)
# встречаются 1–2 раза, по ним силу не оценить. Им ставим фиксированный уровень лиги,
# иначе Ференцварош, обыгрывающий слабых венгров, выглядел бы как Бавария.
LEVEL = {
    "Liga Portugal": 0.85, "Eredivisie": 0.85, "Belgian Pro League": 0.8, "Super Lig": 0.8,
    "Bundesliga": 0.75, "Super League": 0.75, "Premiership": 0.72, "1. Liga": 0.72, "Eliteserien": 0.72,
    "Premier League": 0.7, "Ekstraklasa": 0.68, "HNL": 0.68, "NB I": 0.65, "Ligat ha'Al": 0.65,
    "Cyprus League": 0.63, "Prva Liga": 0.62, "First Professional League": 0.62,
    "Europa League Qualification": 0.62, "Liga Portugal 2": 0.6,
}
CUP_LEVEL = 0.6       # низшие дивизионы в кубках
MINOR_LEVEL = 0.65


def minor_levels(matches, core):
    import collections
    grp = collections.defaultdict(collections.Counter)
    for m in matches:
        for t in (m["h"], m["a"]):
            if t not in core:
                grp[t][m["r"] if m["c"] == "oth" else "#cup"] += 1
    out = {}
    for t, cnt in grp.items():
        name = cnt.most_common(1)[0][0]
        if name == "#cup" or "Cup" in name:
            out[t] = CUP_LEVEL
        else:
            out[t] = LEVEL.get(name, MINOR_LEVEL)
    return out


def fit_ratings(matches, getter, fixed=None, iters=60):
    rows = []
    for m in matches:
        s = m.get("s")
        if not s:
            continue
        xh, xa = getter(s["h"]), getter(s["a"])
        if xh is None or xa is None:
            continue
        age = max((NOW - ts(m["t"])).total_seconds() / 86400, 0)
        rows.append((m["h"], m["a"], xh, xa, 0.5 ** (age / HALF_LIFE)))
    if not rows:
        return None
    sw = sum(r[4] for r in rows)
    mu_h = sum(r[2] * r[4] for r in rows) / sw
    mu_a = sum(r[3] * r[4] for r in rows) / sw
    teams = {r[0] for r in rows} | {r[1] for r in rows}
    fixed = fixed or {}
    att = {t: fixed.get(t, 1.0) for t in teams}
    dfn = {t: 1 / fixed.get(t, 1.0) for t in teams}
    free = [t for t in teams if t not in fixed]
    by = {t: [] for t in teams}
    for h, a, xh, xa, w in rows:
        by[h].append((a, xh, xa, w, True))
        by[a].append((h, xa, xh, w, False))
    base = (mu_h + mu_a) / 2
    for _ in range(iters):
        for t in free:
            num = den = 0.0
            for o, xf, xg_, w, home in by[t]:
                num += w * xf
                den += w * (mu_h if home else mu_a) * dfn[o]
            att[t] = (num + PRIOR_K * base) / (den + PRIOR_K * base)
        for t in free:
            num = den = 0.0
            for o, xf, xa_, w, home in by[t]:
                num += w * xa_
                den += w * (mu_a if home else mu_h) * att[o]
            dfn[t] = (num + PRIOR_K * base) / (den + PRIOR_K * base)
    n = {t: len(by[t]) for t in teams}
    return {"mu": (mu_h, mu_a), "att": att, "def": dfn, "n": n}


def ref_factor(ref):
    """Множитель к ожидаемым ЖК по судье (его средние против средних его лиги)."""
    if not ref or not ref.get("yc") or not ref.get("avg"):
        return 1.0
    n = ref.get("n") or 0
    return (n * ref["yc"] + REF_K * ref["avg"]) / ((n + REF_K) * ref["avg"])


def model_for(T, h, a, R=None, ref=None):
    """Новая модель: λ = база × атака × оборона соперника (рейтинги с затуханием по времени)."""
    old = model_old(T, h, a)
    if not old or not R:
        return old
    lam = {}
    for k in ("g", "c", "y"):
        r = R.get(k)
        if not r or h not in r["att"] or a not in r["att"]:
            lam[k] = None
            continue
        lam[k] = (r["mu"][0] * r["att"][h] * r["def"][a], r["mu"][1] * r["att"][a] * r["def"][h])
    if not lam["g"]:
        return old
    rf = ref_factor(ref)
    if lam["y"]:
        lam["y"] = (lam["y"][0] * rf, lam["y"][1] * rf)
    ha, aa = old["rows"]
    h1 = tot = 0
    for rws in (ha, aa):
        for o, p in rws:
            if o["g"][1] is not None and p["g"][1] is not None:
                h1 += o["g"][1] + p["g"][1]; tot += o["g"][0] + p["g"][0]
    share = min(max(h1 / tot, 0.35), 0.55) if tot else 0.45
    g = lam["g"]
    return {
        "rows": old["rows"], "old": old, "ref": ref, "rf": round(rf, 2),
        "G": joint(*g), "G1": joint(g[0] * share, g[1] * share), "G2": joint(g[0] * (1 - share), g[1] * (1 - share)),
        "C": joint(*lam["c"], disp=DISP["c"]) if lam["c"] else old["C"],
        "Y": joint(*lam["y"], disp=DISP["y"], n=15) if lam["y"] else old["Y"],
        "ly": lam["y"] or old["ly"],
        "lam": {"g": [round(x, 2) for x in g], "c": [round(x, 2) for x in lam["c"]] if lam["c"] else None,
                "y": [round(x, 2) for x in lam["y"]] if lam["y"] else None},
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
    if mtype in ("teamtotals-team1", "teamtotals-team2") and outcome in ("Over", "Under"):
        t = 0 if mtype.endswith("1") else 1
        return (f"{pl}@{t} ИТ{'Б' if over else 'М'} {tl}", "team" if per == 0 else "halves", dist,
                lambda i, j: payout_line((i, j)[t], line, over),
                ("team", t, lambda o, p: payout_line(o["g"][per], line, over) if o["g"][per] is not None else None,
                 lambda o, p: payout_line(p["g"][per], line, over) if p["g"][per] is not None else None))
    if mtype == "bothteamsscore" and outcome in ("Yes", "No") and per != 0:
        yes = outcome == "Yes"
        return (f"{pl}Обе забьют: {'да' if yes else 'нет'}", "halves", dist,
                lambda i, j: ((1.0, 0.0) if (i > 0 and j > 0) == yes else (0.0, 0.0)),
                ("both", lambda o, p: ((1.0, 0.0) if (o["g"][per] > 0 and p["g"][per] > 0) == yes else (0.0, 0.0))
                 if o["g"][per] is not None and p["g"][per] is not None else None))
    if mtype == "totals-cards" and outcome in ("Over", "Under") and per == 0:
        return (f"ЖК {side} {tl}", "cards", "Y",
                lambda i, j: payout_line(i + j, line, over),
                ("both", lambda o, p: payout_line(o["y"][0] + p["y"][0], line, over) if val(o, "y") is not None and val(p, "y") is not None else None))
    if mtype in ("teamtotals-cards-team1", "teamtotals-cards-team2") and outcome in ("Over", "Under") and per == 0:
        t = 0 if mtype.endswith("1") else 1
        return (f"@{t} ЖК ИТ{'Б' if over else 'М'} {tl}", "cards", "Y",
                lambda i, j: payout_line((i, j)[t], line, over),
                ("team", t, lambda o, p: payout_line(o["y"][0], line, over) if val(o, "y") is not None else None,
                 lambda o, p: payout_line(p["y"][0], line, over) if val(p, "y") is not None else None))
    if per != 0 and mtype not in ("1x2", "doublechance"):
        return None
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
        return (pl + {"1": "П1", "X": "Ничья", "2": "П2"}[outcome], "result" if per == 0 else "halves", dist,
                lambda i, j: (1.0, 0.0) if f(i, j) else (0.0, 0.0), None)
    if mtype == "doublechance" and outcome in ("1X", "12", "2X", "X2"):
        f = {"1X": lambda i, j: i >= j, "12": lambda i, j: i != j, "2X": lambda i, j: j >= i, "X2": lambda i, j: j >= i}[outcome]
        return (pl + {"1X": "1X", "12": "12", "2X": "X2", "X2": "X2"}[outcome], "result" if per == 0 else "halves", dist,
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


CARD_MARGIN = 1.05      # «брать от» = справедливый кэф + 5%


def card_lines(model):
    """Тоталы ЖК без линии букмекера: модель + факты, справедливый кэф."""
    Y, ly = model.get("Y"), model.get("ly")
    if not Y or not ly:
        return []
    out = []
    mu = ly[0] + ly[1]
    specs = []
    for line in sorted({math.floor(mu) - 0.5, math.floor(mu) + 0.5, math.floor(mu) + 1.5}):
        if line > 0:
            for over in (True, False):
                specs.append((f"ЖК {'ТБ' if over else 'ТМ'} {line:g}", (lambda L, O: lambda i, j: payout_line(i + j, L, O))(line, over),
                              ("both", (lambda L, O: lambda o, p: payout_line(o["y"][0] + p["y"][0], L, O)
                                        if val(o, "y") is not None and val(p, "y") is not None else None)(line, over))))
    for t in (0, 1):
        m = ly[t]
        for line in sorted({max(math.floor(m) - 0.5, 0.5), math.floor(m) + 0.5, math.floor(m) + 1.5}):
            for over in (True, False):
                specs.append((f"@{t} ЖК ИТ{'Б' if over else 'М'} {line:g}",
                              (lambda L, O, T: lambda i, j: payout_line((i, j)[T], L, O))(line, over, t),
                              ("team", t, (lambda L, O: lambda o, p: payout_line(o["y"][0], L, O) if val(o, "y") is not None else None)(line, over),
                               (lambda L, O: lambda o, p: payout_line(p["y"][0], L, O) if val(p, "y") is not None else None)(line, over))))
    for label, fn, fspec in specs:
        w = sum(p * fn(i, j)[0] for i, j, p in Y)
        fr = fact(model, fspec)
        fw = fr[0] if fr else None
        bw = (w + fw) / 2 if fw is not None else w
        if bw <= 0.05:
            continue
        fair = 1 / bw
        out.append({"label": label, "p": round(bw, 3), "pm": round(w, 3), "pf": round(fw, 3) if fw is not None else None,
                    "fair": round(fair, 2), "from": round(fair * CARD_MARGIN, 2),
                    "agree": abs(w - fw) <= AGREE if fw is not None else None})
    return out


def main():
    odds = load("odds.json")
    meta = load("meta.json")
    db = load("matches.json", {"matches": []})
    if not meta:
        print("Нет данных")
        return 0
    odds = odds or {"fixtures": []}
    T = Teams(db["matches"])
    core = set()
    for tbl in (meta.get("standings") or {}).values():
        core |= set(tbl)
    lv = minor_levels(db["matches"], core)
    R = {"g": fit_ratings(db["matches"], goal_val, lv),
         "c": fit_ratings(db["matches"], lambda sd: val(sd, "c"), lv),
         "y": fit_ratings(db["matches"], lambda sd: val(sd, "y"), {t: 1.0 for t in lv})}
    refs = {k: v.get("ref") for k, v in (load("refs.json", {}) or {}).items()}
    names = meta["teams"]
    ours = []
    for code, u in meta["upcoming"].items():
        for blk in (u, u.get("next") or {}):
            for m in blk.get("matches") or []:
                ours.append({**m, "c": code, "round": blk.get("round", "")})

    # Сопоставление матчей: внутри турнира и окна ±3 ч, жадно по сходству названий
    # (русские названия Фонбета переводятся транслитом + словарём).
    cand = []
    for f in odds["fixtures"]:
        t = ts(f["t"])
        n1, n2 = ru2lat(f["n1"]), ru2lat(f["n2"])
        slot = [m for m in ours if m["c"] == f["c"] and abs((ts(m["t"]) - t).total_seconds()) <= 3 * 3600]
        for m in slot:
            sc = sim(names[m["h"]]["name"], n1) + sim(names[m["a"]]["name"], n2)
            cand.append((sc + (0.3 if len(slot) == 1 else 0), id(f), m["id"], m))
    cand.sort(key=lambda x: -x[0])
    pairing, used_m = {}, set()
    for sc, fid, mid, m in cand:
        if fid in pairing or mid in used_m or sc < 0.9:
            continue
        pairing[fid] = m
        used_m.add(mid)

    bets = []
    log_rows = []
    matched = unmatched = 0
    for f in odds["fixtures"]:
        t = ts(f["t"])
        if t < NOW - timedelta(minutes=5):
            continue
        best = pairing.get(id(f))
        if not best:
            unmatched += 1
            print(f"  не сопоставлен: {f['c']} {f['t']} {f['n1']} – {f['n2']}")
            continue
        matched += 1
        model = model_for(T, best["h"], best["a"], R, refs.get(str(best["id"])))
        if not model:
            continue
        old = model.get("old")
        team_names = (names[best["h"]]["name"], names[best["a"]]["name"])
        # Вероятность по рынку без маржи: нормируем внутри одного рынка
        grp = {}
        for mtype, period, line, outcome, price in f["odds"]:
            grp.setdefault((mtype, period, line), []).append(price)
        def market_p(mtype, period, line, price):
            ps = grp.get((mtype, period, line), [])
            target = 2.0 if mtype == "doublechance" else 1.0
            if mtype.startswith("1x2") and len(ps) < 3 or mtype != "1x2" and mtype != "doublechance" and len(ps) < 2:
                return None
            tot = sum(1 / x for x in ps)
            return (1 / price) * target / tot if tot else None
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
            Do = old.get(dist) if old else None
            wo = sum(p * fn(i, j)[0] for i, j, p in Do) if Do else None
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
            own = bw
            mk = market_p(mtype, period, line, price)
            if mk is not None:
                # Рынок знает про силу соперника, составы и мотивацию, а модель нет:
                # берём середину между нашей оценкой и рынком.
                bw = MARKET_W * mk * (1 - bp) + (1 - MARKET_W) * bw
            if bw <= 0.02:
                continue
            ev = bw * price + bp - 1
            fair = (1 - bp) / bw
            label = label.replace("@0", team_names[0]).replace("@1", team_names[1])
            if 1.3 <= price <= 4:
                log_rows.append({"k": "|".join(map(str, [best["id"], mtype, period, line, outcome])),
                                 "mid": best["id"], "c": best["c"], "t": best["t"], "h": best["h"], "a": best["a"],
                                 "m": [mtype, period, line, outcome], "label": label, "kind": kind,
                                 "odds": price, "p": round(bw, 4), "push": round(bp, 4), "pm": round(w, 4),
                                 "pmo": round(wo, 4) if wo is not None else None,
                                 "pf": round(fw, 4) if fw is not None else None,
                                 "pk": round(mk * (1 - bp), 4) if mk is not None else None,
                                 "rf": model.get("rf")})
            if ev < -0.08 or not (1.2 <= price <= 5):
                continue
            bets.append({
                "mid": best["id"], "c": best["c"], "t": best["t"], "h": best["h"], "a": best["a"],
                "label": label, "kind": kind, "odds": price, "m": [mtype, period, line, outcome],
                "p": round(bw, 3), "push": round(bp, 3), "pm": round(w, 3), "po": round(own, 3),
                "pk": round(mk * (1 - bp), 3) if mk is not None else None,
                "pf": round(fw, 3) if fw is not None else None,
                "fair": round(fair, 2), "ev": round(ev, 3), "agree": agree,
            })
    bets.sort(key=lambda b: -b["ev"])

    # ЖК: Фонбет их не отдаёт, поэтому считаем справедливые кэфы для всех ближайших матчей
    cards = []
    for m in ours:
        t = ts(m["t"])
        if not (NOW < t < NOW + timedelta(days=7)) or m["c"] == "rpl":
            continue
        model = model_for(T, m["h"], m["a"], R, refs.get(str(m["id"])))
        if not model:
            continue
        tn = (names[m["h"]]["name"], names[m["a"]]["name"])
        lines = card_lines(model)
        for x in lines:
            x["label"] = x["label"].replace("@0", tn[0]).replace("@1", tn[1])
        if lines:
            rf = model.get("ref") or {}
            cards.append({"mid": m["id"], "c": m["c"], "t": m["t"], "h": m["h"], "a": m["a"],
                          "ly": [round(v, 2) for v in model["ly"]], "lines": lines,
                          "ref": {"name": rf.get("name"), "yc": rf.get("yc"), "avg": rf.get("avg"), "n": rf.get("n")} if rf else None})
    cards.sort(key=lambda x: x["t"])
    import tracklog
    tracklog.update(log_rows, db["matches"], NOW, spec)
    out = {"updated": NOW.isoformat(timespec="seconds"), "oddsUpdated": odds.get("updated"),
           "bookmaker": odds.get("bookmaker"), "bets": bets, "cards": cards}
    with open(os.path.join(DATA, "value.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"Сопоставлено матчей {matched}, нет пары {unmatched}, ставок в файле {len(bets)}, "
          f"с перевесом 5%+ и согласием {sum(1 for b in bets if b['ev'] >= 0.05 and b['agree'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
