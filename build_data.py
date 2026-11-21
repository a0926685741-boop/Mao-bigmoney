#!/usr/bin/env python3
# 由 GitHub Actions 每天執行：抓資料 -> 產生 data.json（只用標準函式庫，不需 pip）
import os, json, urllib.request, urllib.parse, datetime as dt

TOKEN = os.environ.get("FINMIND_TOKEN", "")
UA = {"User-Agent": "Mozilla/5.0"}
ERR = {}


def get(url, hdr=None):
    r = urllib.request.Request(url, headers={**UA, **(hdr or {})})
    return json.loads(urllib.request.urlopen(r, timeout=60).read().decode("utf-8-sig"))


def fm(ds, sid=None, start=None):
    q = {"dataset": ds}
    if sid: q["data_id"] = sid
    if start: q["start_date"] = start
    if TOKEN: q["token"] = TOKEN
    j = get("https://api.finmindtrade.com/api/v4/data?" + urllib.parse.urlencode(q),
            {"Authorization": "Bearer " + TOKEN} if TOKEN else None)
    if j.get("status") != 200:
        raise RuntimeError(f"{ds}: {j.get('msg')}")
    return j["data"]


def num(x):
    try: return float(str(x).replace(",", "").replace("+", ""))
    except Exception: return None


def snapshot():
    """全市場當日收盤與漲跌：證交所＋櫃買開放資料。回傳 {代號: {n,p,v}}"""
    out = {}
    try:
        for r in get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"):
            c, ch = num(r.get("ClosingPrice")), num(r.get("Change"))
            if c and ch is not None and c - ch > 0:
                out[r["Code"]] = {"n": r["Name"], "p": ch / (c - ch) * 100, "v": num(r.get("TradeValue")) or 0}
    except Exception as e:
        ERR["market"] = f"上市資料失敗：{e}"
    try:
        for r in get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"):
            c, ch = num(r.get("Close")), num(r.get("Change"))
            if c and ch is not None and c - ch > 0:
                out[r["SecuritiesCompanyCode"]] = {"n": r["CompanyName"], "p": ch / (c - ch) * 100,
                                                   "v": num(r.get("TransactionAmount")) or 0}
    except Exception as e:
        ERR["market"] = ERR.get("market", "") + f" 上櫃資料失敗：{e}"
    return out


def pack(d, snap):
    res = []
    for name, ids in d.items():
        rows = [(c, snap[c]) for c in ids if c in snap]
        if len(rows) < 3: continue
        top = sorted(rows, key=lambda x: -x[1]["p"])[:8]
        res.append({"name": name, "n": len(rows), "avg": round(sum(r[1]["p"] for r in rows) / len(rows), 2),
                    "up": sum(1 for r in rows if r[1]["p"] > 0), "val": round(sum(r[1]["v"] for r in rows) / 1e8, 1),
                    "top": [{"c": c, "n": s["n"], "p": round(s["p"], 2)} for c, s in top]})
    return sorted(res, key=lambda x: -x["avg"])


def stock(sid, start):
    o = {}
    px = fm("TaiwanStockPrice", sid, start)
    o["d"] = [[r["date"], r["open"], r["max"], r["min"], r["close"], round(r["Trading_Volume"] / 1000)]
              for r in px if r["close"] > 0 and r["open"] > 0]
    try:
        m = {}
        for r in fm("TaiwanStockInstitutionalInvestorsBuySell", sid, start):
            k = {"Foreign_Investor": 1, "Investment_Trust": 2}.get(r["name"])
            if k:
                m.setdefault(r["date"], [0, 0])[k - 1] += (r["buy"] - r["sell"]) / 1000
        o["inst"] = [[d, round(v[0]), round(v[1])] for d, v in sorted(m.items())][-150:]
    except Exception as e:
        ERR[sid + "_inst"] = str(e)
    try:
        o["mg"] = [[r["date"], r["MarginPurchaseTodayBalance"],
                    r["MarginPurchaseTodayBalance"] - r["MarginPurchaseYesterdayBalance"]]
                   for r in fm("TaiwanStockMarginPurchaseShortSale", sid, start)][-150:]
    except Exception as e:
        ERR[sid + "_mg"] = str(e)
    return o


def main():
    pool = [l.split("#")[0].strip() for l in open("pool.txt", encoding="utf-8")]
    pool = [p for p in pool if p]
    concepts = json.load(open("concepts.json", encoding="utf-8")) if os.path.exists("concepts.json") else {}
    info = {}
    try:
        for r in fm("TaiwanStockInfo"):
            info[r["stock_id"]] = r
    except Exception as e:
        ERR["info"] = str(e)
    snap = snapshot()
    ind = {}
    for sid, r in info.items():
        if sid in snap and len(sid) == 4 and sid.isdigit() and not sid.startswith("0"):
            ind.setdefault(r["industry_category"], []).append(sid)
    start = (dt.date.today() - dt.timedelta(days=900)).isoformat()
    stocks = {}
    for sid in pool:
        try:
            s = stock(sid, start)
            s["name"] = info.get(sid, {}).get("stock_name") or snap.get(sid, {}).get("n", sid)
            s["ind"] = info.get(sid, {}).get("industry_category", "")
            stocks[sid] = s
        except Exception as e:
            ERR[sid] = str(e)
    out = {"updated": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
           "groups": {"ind": pack(ind, snap), "con": pack(concepts, snap)}, "stocks": stocks, "errors": ERR}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("完成；錯誤：", ERR or "無")


if __name__ == "__main__":
    main()
