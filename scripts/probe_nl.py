import json, urllib.request
def get(p):
    r=urllib.request.Request("https://www.fotmob.com/api/data"+p,headers={"User-Agent":"Mozilla/5.0 (f-stats personal project)"})
    return json.load(urllib.request.urlopen(r,timeout=40))
for i in list(range(9805,9812))+[10040,10041,10042]:
    try:
        d=get(f"/leagues?id={i}")
        det=d.get("details") or {}
        fx=(d.get("fixtures") or {}).get("allMatches") or []
        print("ID",i,det.get("name"),det.get("selectedSeason"),"matches",len(fx), fx[0]["home"]["name"]+" - "+fx[0]["away"]["name"] if fx else "")
    except Exception as e: print("ID",i,"ERR",e)
