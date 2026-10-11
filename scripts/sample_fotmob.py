"""Временный скрипт: сохраняет образцы ответов FotMob для разработки."""
import json, os, time
import urllib.request

B = "https://www.fotmob.com/api/data"
H = {"User-Agent": "Mozilla/5.0"}
os.makedirs("samples", exist_ok=True)
LEAGUES = {"pl": 47, "ll": 87, "sa": 55, "bl": 54, "l1": 53, "cl": 42, "el": 73,
           "fa": 132, "efl": 133, "cdr": 138, "cit": 141, "dfb": 209, "cdf": 134}
summary = []

def get(path):
    req = urllib.request.Request(B + path, headers=H)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)

pl_match = cl_match = None
for code, lid in LEAGUES.items():
    try:
        d = get(f"/leagues?id={lid}")
        allm = d["fixtures"]["allMatches"]
        done = [m for m in allm if m["status"].get("finished")]
        summary.append(f"{code} {lid}: {d['details'].get('name')} season={d['details'].get('selectedSeason')} matches={len(allm)} finished={len(done)}")
        if code == "pl":
            json.dump(d, open("samples/league_pl.json", "w"), ensure_ascii=False)
            pl_match = done[-1]["id"]
        if code == "cl":
            json.dump({"fixtures": d["fixtures"], "details": d["details"], "table": d.get("table")}, open("samples/league_cl.json", "w"), ensure_ascii=False)
            cl_match = done[-1]["id"] if done else None
        if code == "fa":
            json.dump({"fixtures": d["fixtures"], "details": d["details"]}, open("samples/league_fa.json", "w"), ensure_ascii=False)
    except Exception as e:
        summary.append(f"{code} {lid}: ERROR {type(e).__name__} {e}")
    time.sleep(1)

for name, mid in (("pl", pl_match), ("cl", cl_match)):
    if mid:
        json.dump(get(f"/matchDetails?matchId={mid}"), open(f"samples/match_{name}.json", "w"), ensure_ascii=False)
        time.sleep(1)
open("samples/summary.txt", "w").write("\n".join(summary) + "\n")
print("\n".join(summary))
