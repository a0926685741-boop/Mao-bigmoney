#!/usr/bin/env python3
# 大盤籌碼（全部用官方免費來源，不需要 FinMind）：小台指散戶多空比、大盤融資餘額
# 每個交易日盤後由 GitHub Actions 執行，寫入 chip.json（保留近 60 筆歷史）
import csv, io, json, os, urllib.request, urllib.parse, datetime as dt
UA = {"User-Agent": "Mozilla/5.0"}
ERR = {}


def post(url, data):
    r = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers=UA)
    raw = urllib.request.urlopen(r, timeout=60).read()
    for enc in ("utf-8-sig", "big5"):
        try: return raw.decode(enc)
        except Exception: pass
    return raw.decode("utf-8", "ignore")


def num(x):
    try: return float(str(x).replace(",", "").strip())
    except Exception: return None


def rows(txt):
    return [r for r in csv.reader(io.StringIO(txt)) if r]


def col(head, *keys):
    for i, h in enumerate(head):
        if all(k in h for k in keys): return i
    return None


def market_oi(d0, d1):
    """小型臺指(MTX)全市場未沖銷量：每日行情下載，只取一般交易時段、排除價差、各到期月加總"""
    t = rows(post("https://www.taifex.com.tw/cht/3/futDataDown",
                  {"down_type": "1", "commodity_id": "MTX", "queryStartDate": d0, "queryEndDate": d1}))
    h = t[0]; iD, iO, iS, iP = col(h, "交易日期"), col(h, "未沖銷"), col(h, "交易時段"), col(h, "價差")
    iM = col(h, "到期月")
    out = {}
    for r in t[1:]:
        if len(r) <= max(iD, iO): continue
        if iS is not None and len(r) > iS and "一般" not in r[iS]: continue
        if iM is not None and "/" in r[iM]: continue           # 價差合約（月份欄含 /）
        if iP is not None and len(r) > iP and r[iP].strip(): continue
        v = num(r[iO])
        if v is not None: out[r[iD].strip()] = out.get(r[iD].strip(), 0) + v
    return out


def inst_oi(d0, d1):
    """三大法人在小型臺指(MXF)的多方、空方未平倉口數（自營商＋投信＋外資）"""
    t = rows(post("https://www.taifex.com.tw/cht/3/futContractsDateDown",
                  {"queryStartDate": d0, "queryEndDate": d1, "commodityId": "MXF"}))
    h = t[0]; iD, iL, iS = col(h, "日期"), col(h, "多方", "未平倉", "口數"), col(h, "空方", "未平倉", "口數")
    out = {}
    for r in t[1:]:
        if len(r) <= max(iD, iL, iS): continue
        l, s = num(r[iL]), num(r[iS])
        if l is None or s is None: continue
        a = out.setdefault(r[iD].strip(), [0, 0]); a[0] += l; a[1] += s
    return out


def retail_ratio(d0, d1):
    m, i = market_oi(d0, d1), inst_oi(d0, d1)
    res = []
    for d in sorted(set(m) & set(i)):
        tot = m[d]
        if tot <= 0: continue
        rl, rs = tot - i[d][0], tot - i[d][1]
        res.append([d.replace("/", "-"), round((rl - rs) / tot * 100, 2), round(rl), round(rs), round(tot)])
    return res


def margin_total():
    """證交所免費開放資料：大盤融資餘額（金額）與融券。欄位名稱以印出的第一筆為準，抓不到就留空"""
    j = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN", headers=UA), timeout=60).read().decode("utf-8-sig"))
    tot = {"margin_amt": 0.0, "n": 0}
    for r in j:
        for k, v in r.items():
            if "融資今日餘額" in k and num(v): tot["margin_amt"] += num(v); tot["n"] += 1
    return tot if tot["n"] else None


def main():
    old = {}
    if os.path.exists("chip.json"):
        try: old = json.load(open("chip.json", encoding="utf-8"))
        except Exception: pass
    today = dt.date.today()
    d0, d1 = (today - dt.timedelta(days=45)).strftime("%Y/%m/%d"), today.strftime("%Y/%m/%d")
    try:
        new = retail_ratio(d0, d1)
    except Exception as e:
        ERR["retail"] = str(e); new = []
    hist = {x[0]: x for x in old.get("retail", [])}
    for x in new: hist[x[0]] = x
    mg = None
    try: mg = margin_total()
    except Exception as e: ERR["margin"] = str(e)
    mh = {x[0]: x for x in old.get("margin", [])}
    if mg: mh[today.isoformat()] = [today.isoformat(), round(mg["margin_amt"])]
    out = {"updated": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
           "retail": [hist[k] for k in sorted(hist)][-60:], "margin": [mh[k] for k in sorted(mh)][-60:],
           "tw_dash": "https://www.twse.com.tw/", "errors": ERR}
    json.dump(out, open("chip.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("小台散戶多空比", len(out["retail"]), "筆；融資", len(out["margin"]), "筆；錯誤：", ERR or "無")


if __name__ == "__main__":
    main()
