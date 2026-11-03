"""Разовая проверка: где взять названия команд для матчей OddsPapi."""
import json, os, urllib.request, urllib.parse, gzip, time, urllib.error, atexit
OUT = "probe"; os.makedirs(OUT, exist_ok=True)
KEY = os.environ.get("ODDSPAPI_KEY", "").strip()
lines = []
atexit.register(lambda: open(f"{OUT}/odds.md", "w").write("\n".join(lines) + "\n"))
def log(*a):
    s = " ".join(str(x) for x in a); lines.append(s); print(s)
def op(path, **params):
    params["apiKey"] = KEY
    req = urllib.request.Request("https://api.oddspapi.io/v4/" + path + "?" + urllib.parse.urlencode(params), headers={"Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = r.read()
            if r.headers.get("Content-Encoding") == "gzip": d = gzip.decompress(d)
            return json.loads(d)
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "body": e.read()[:300].decode("utf-8", "replace")}
for path, params in (("fixtures", {"tournamentId": 7}), ("participants", {"sportId": 10})):
    r = op(path, **params)
    if isinstance(r, dict):
        log(path, "->", str(r)[:400])
    else:
        log(path, "-> список", len(r)); log("  пример:", json.dumps(r[:2], ensure_ascii=False)[:700])
        json.dump(r[:50] if path == "fixtures" else r, open(f"{OUT}/{path}.json", "w"), ensure_ascii=False)
    time.sleep(2)
