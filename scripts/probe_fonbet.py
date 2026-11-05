import json, urllib.request, gzip, re
H={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36","Accept-Encoding":"gzip"}
def get(u):
    r=urllib.request.urlopen(urllib.request.Request(u,headers=H),timeout=60)
    d=r.read()
    if r.headers.get("Content-Encoding")=="gzip": d=gzip.decompress(d)
    return d
B="https://line-lb51.bk6bba-resources.com"
for path in ["/line/factorsCatalog?lang=ru&version=0","/events/factorsCatalog?lang=ru&version=0","/line/catalog?lang=ru","/catalog/factors?lang=ru","/line/factorsCatalog?lang=ru"]:
    try:
        d=get(B+path);print("CAT OK",path,len(d),d[:300]);open("probe/catalog.json","wb").write(d);break
    except Exception as e: print("CAT FAIL",path,e)
j=json.loads(get(B+"/events/list?lang=ru&version=0&scopeMarket=1600"))
sports={s["id"]:s for s in j["sports"]}
want=re.compile(r"Англия\. Премьер|Испания\. Примера|Испания\. Ла Лига|Италия\. Серия А|Германия\. Бундеслига|Франция\. Лига 1|Лига чемпионов УЕФА|Лига Европы УЕФА|Россия\. Премьер", re.I)
seg={sid for sid,s in sports.items() if s.get("kind")=="segment" and want.search(s.get("name",""))}
print("segments",[sports[s]["name"] for s in seg])
roots={e["id"] for e in j["events"] if e.get("sportId") in seg and e.get("level")==1}
evs=[e for e in j["events"] if e["id"] in roots or e.get("parentId") in roots]
ids={e["id"] for e in evs}
cf=[c for c in j["customFactors"] if c["e"] in ids]
import os; os.makedirs("probe",exist_ok=True)
json.dump({"sports":[sports[s] for s in seg],"events":evs,"customFactors":cf,"eventMiscs":[m for m in j.get("eventMiscs",[]) if m.get("id") in ids]},open("probe/fonbet_ours.json","w"),ensure_ascii=False)
from collections import Counter
print("roots",len(roots),"events",len(evs),"factors",sum(len(c["factors"]) for c in cf))
print(Counter(e.get("name","") for e in evs if e.get("level")!=1).most_common(60))
