#!/usr/bin/env python3
# 指數小欄位：約每 15 分鐘由 GitHub Actions 執行，寫入 market.json（延遲約 10–20 分鐘，非即時）
import json, urllib.request, datetime as dt
UA = {"User-Agent": "Mozilla/5.0"}


def yh(sym):
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
    r = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read())["chart"]["result"][0]
    p = r["meta"]["regularMarketPrice"]
    cl = [x for x in r["indicators"]["quote"][0]["close"] if x is not None]   # 最近幾個交易日收盤
    ref = cl[-2] if len(cl) >= 2 else None
    return p, (p / ref - 1) * 100 if ref else None


def txf():    # 台指期近月：期交所即時報價（非正式介面；抓不到就顯示 —）
    body = json.dumps({"MarketType": "0", "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "",
                       "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"}).encode()
    r = urllib.request.Request("https://mis.taifex.com.tw/futures/api/getQuoteList", data=body,
                               headers={**UA, "Content-Type": "application/json"})
    for x in json.loads(urllib.request.urlopen(r, timeout=30).read())["RtData"]["QuoteList"]:
        if "-" not in x.get("SymbolID", "") and x.get("CLastPrice"):
            p, ref = float(x["CLastPrice"]), float(x.get("CRefPrice") or 0)
            return p, (p / ref - 1) * 100 if ref else None


items = []
for n, fn in (("加權", lambda: yh("%5ETWII")), ("台指期", txf), ("日經", lambda: yh("%5EN225")),
              ("費半", lambda: yh("%5ESOX")), ("那斯達克", lambda: yh("%5EIXIC"))):
    try:
        p, c = fn()
        items.append({"n": n, "p": round(p, 2), "c": None if c is None else round(c, 2)})
    except Exception as e:
        print(n, "失敗：", e)
        items.append({"n": n, "p": None, "c": None})
json.dump({"updated": dt.datetime.utcnow().strftime("%m-%d %H:%M UTC"), "items": items},
          open("market.json", "w", encoding="utf-8"), ensure_ascii=False)
