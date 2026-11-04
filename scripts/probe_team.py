import json, urllib.request, sys
def get(p):
    r=urllib.request.Request("https://www.fotmob.com/api/data"+p,headers={"User-Agent":"Mozilla/5.0 (f-stats personal project)"})
    return json.load(urllib.request.urlopen(r,timeout=40))
d=get("/teams?id="+sys.argv[1])
print("keys",list(d.keys()))
fx=d.get("fixtures") or {}
print("fixtures keys",list(fx.keys()))
af=fx.get("allFixtures") or {}
print("allFixtures keys",list(af.keys()) if isinstance(af,dict) else type(af))
lst=af.get("fixtures") if isinstance(af,dict) else af
print("n",len(lst or []))
for f in (lst or [])[:40]:
    print(json.dumps(f,ensure_ascii=False)[:400])
# v2
