import json, urllib.request, gzip, sys
H={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36","Accept-Encoding":"gzip"}
def get(u):
    r=urllib.request.urlopen(urllib.request.Request(u,headers=H),timeout=30)
    d=r.read()
    if r.headers.get("Content-Encoding")=="gzip": d=gzip.decompress(d)
    return r.status,d
for u in ["https://fon.bet/urls.json","https://www.fon.bet/urls.json","https://www.fonbet.ru/urls.json","https://pari.ru/urls.json"]:
    try:
        s,d=get(u);print("OK",u,s,d[:600])
    except Exception as e: print("FAIL",u,type(e).__name__,e)
hosts=[]
try:
    s,d=get("https://fon.bet/urls.json");j=json.loads(d);print(json.dumps(j)[:1500])
    hosts=[h for h in j.get("line",[])]
except Exception as e: print("urls err",e)
hosts+= ["//line01w.bk6bba-resources.com","//line02w.bk6bba-resources.com","//line31w.bk6bba-resources.com","//line55w.bk6bba-resources.com"]
for h in hosts[:8]:
    u=("https:"+h if h.startswith("//") else h)+"/events/list?lang=ru&version=0&scopeMarket=1600"
    try:
        s,d=get(u);j=json.loads(d)
        print("LINE OK",u,len(d),list(j.keys()))
        sp=j.get("sports",[]);ev=j.get("events",[]);cf=j.get("customFactors",[])
        print(" sports",len(sp),"events",len(ev),"customFactors",len(cf))
        print(" sport sample",json.dumps(sp[:3],ensure_ascii=False)[:600])
        print(" event sample",json.dumps(ev[:2],ensure_ascii=False)[:800])
        print(" cf sample",json.dumps(cf[:1],ensure_ascii=False)[:1500])
        open("fonbet_list.json","wb").write(d)
        break
    except Exception as e: print("LINE FAIL",u,type(e).__name__,e)
