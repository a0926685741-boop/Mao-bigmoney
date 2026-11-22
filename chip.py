#!/usr/bin/env python3
# 大盤籌碼（全部用官方免費來源，不需要 FinMind）：小台指散戶多空比、大盤融資餘額
# 每個交易日盤後由 GitHub Actions 執行，寫入 chip.json（保留近 60 筆歷史）
import csv, io, json, os, re, time, urllib.request, urllib.parse, datetime as dt
UA = {"User-Agent": "Mozilla/5.0"}
ERR = {}


def post(url, data):
    """像瀏覽器一樣送出表單；遇到 5xx（例如 520）最多重試 3 次"""
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Referer": "https://www.taifex.com.tw/cht/3/futDailyMarketReport",
           "Origin": "https://www.taifex.com.tw", "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Content-Type": "application/x-www-form-urlencoded"}
    err = None
    for k in range(3):
        try:
            r = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers=hdr)
            raw = urllib.request.urlopen(r, timeout=60).read()
            break
        except Exception as e:
            err = e; time.sleep(5 * (k + 1))
    else:
        raise RuntimeError(url.rsplit("/", 1)[-1] + " " + str(err))
    if raw.lstrip()[:1] == b"<":      # 回傳網頁而不是 CSV：多半是查詢錯誤，把文字訊息印出來
        raise RuntimeError(url.rsplit("/", 1)[-1] + " 回傳網頁：" + re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw.decode("utf-8", "ignore")))[:200])
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


def chunks(d0, d1, n=27):
    """期交所下載每次最多約一個月，分段查詢"""
    a = dt.datetime.strptime(d0, "%Y/%m/%d").date(); z = dt.datetime.strptime(d1, "%Y/%m/%d").date()
    while a <= z:
        b = min(a + dt.timedelta(days=n), z)
        yield a.strftime("%Y/%m/%d"), b.strftime("%Y/%m/%d")
        a = b + dt.timedelta(days=1)


def market_oi(d0, d1):
    out = {}
    for a, b in chunks(d0, d1):
        for k, v in market_oi1(a, b).items(): out[k] = v
    return out


def inst_oi(d0, d1):
    out = {}
    for a, b in chunks(d0, d1):
        try:
            got = inst_oi1(a, b)
        except RuntimeError as e:            # 區間查詢被拒絕時，改成逐日查詢（只查平日）
            got = {}
            d = dt.datetime.strptime(a, "%Y/%m/%d").date(); z = dt.datetime.strptime(b, "%Y/%m/%d").date()
            while d <= z:
                if d.weekday() < 5:
                    try: got.update(inst_oi1(d.strftime("%Y/%m/%d"), d.strftime("%Y/%m/%d")))
                    except Exception: pass
                d += dt.timedelta(days=1)
            if not got: raise e
        out.update(got)
    return out


def market_oi1(d0, d1):
    """小型臺指(MTX)全市場未沖銷量：每日行情下載，只取一般交易時段、排除價差、各到期月加總"""
    t = rows(post("https://www.taifex.com.tw/cht/3/futDataDown",
                  {"down_type": "1", "commodity_id": "MTX", "queryStartDate": d0, "queryEndDate": d1}))
    h = t[0]; iD, iO, iS = col(h, "交易日期"), col(h, "未沖銷"), col(h, "交易時段")
    iM = col(h, "到期月"); iP = None
    if iD is None or iO is None:
        raise RuntimeError("MTX 欄位對不上；標題=" + "|".join(h)[:160] + "；列數=" + str(len(t)))
    out = {}
    for r in t[1:]:
        if len(r) <= max(iD, iO): continue
        if iS is not None and len(r) > iS and "一般" not in r[iS]: continue
        if iM is not None and "/" in r[iM]: continue
        if iP is not None and len(r) > iP and r[iP].strip(): continue
        v = num(r[iO])
        if v is not None: out[r[iD].strip()] = out.get(r[iD].strip(), 0) + v
    return out


DATE_FMTS = ["%Y/%m/%d", "%Y-%m-%d", "%Y/%-m/%-d"]


def inst_oi1(d0, d1):
    """三大法人在小型臺指(MXF)的多方、空方未平倉口數（自營商＋投信＋外資）；日期格式不被接受時換下一種"""
    last = None
    for fmt in DATE_FMTS:
        a = dt.datetime.strptime(d0, "%Y/%m/%d").strftime(fmt); b = dt.datetime.strptime(d1, "%Y/%m/%d").strftime(fmt)
        try:
            t = rows(post("https://www.taifex.com.tw/cht/3/futContractsDateDown",
                          {"queryStartDate": a, "queryEndDate": b, "commodityId": "MXF"}))
        except RuntimeError as e:
            last = e
            if "日期時間" in str(e): continue
            raise
        h = t[0]; iD, iL, iS = col(h, "日期"), col(h, "多方", "未平倉", "口數"), col(h, "空方", "未平倉", "口數")
        if None in (iD, iL, iS):
            raise RuntimeError("MXF 欄位對不上；標題=" + "|".join(h)[:160] + "；列數=" + str(len(t)))
        out = {}
        for r in t[1:]:
            if len(r) <= max(iD, iL, iS): continue
            l, s2 = num(r[iL]), num(r[iS])
            if l is None or s2 is None: continue
            x = out.setdefault(r[iD].strip(), [0, 0]); x[0] += l; x[1] += s2
        return out
    raise last


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
    """證交所免費開放資料：大盤融資餘額（金額）與融券"""
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
