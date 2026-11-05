import json, urllib.request
def get(p):
    r=urllib.request.Request("https://www.fotmob.com/api/data"+p,headers={"User-Agent":"Mozilla/5.0 (f-stats personal project)"})
    return json.load(urllib.request.urlopen(r,timeout=40))
db=json.load(open("data/matches.json"))["matches"]
meta=json.load(open("data/meta.json"))
up=[m for u in meta["upcoming"].values() for m in u["matches"]][:1]
for mid in [db[-1]["id"], up[0]["id"] if up else None]:
    if not mid: continue
    d=get(f"/matchDetails?matchId={mid}")
    ib=((d.get("content") or {}).get("matchFacts") or {}).get("infoBox")
    print("MATCH",mid,"infoBox:",json.dumps(ib,ensure_ascii=False)[:800])
    def walk(o,path=""):
        if isinstance(o,dict):
            for k,v in o.items():
                if "refer" in k.lower(): print("  KEY",path+"/"+k,json.dumps(v,ensure_ascii=False)[:300])
                walk(v,path+"/"+k)
        elif isinstance(o,list):
            for i,v in enumerate(o[:50]): walk(v,path+f"[{i}]")
    walk(d)
