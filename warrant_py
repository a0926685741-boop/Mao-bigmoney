# -*- coding: utf-8 -*-
"""權證資料：證交所＋櫃買。輸出 warrants.json（個別權證、含 IV/Delta/槓桿/差槓比）與 warrant_hist.json（每日各標的成交金額，供 2 日/15 日排行）。
只用標準函式庫。隱含波動率、Delta 為依 Black-Scholes 自行估算（無風險利率 1.5%、不計股利，歐式近似），僅供參考。"""
import json, math, re, os, sys, time, datetime, statistics, urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}
LOG = []
R_FREE = 0.015
KEEP_DAYS = 25      # 歷史保留交易日數
NEED_DAYS = 16      # 回補近 16 個交易日（涵蓋 15 日排行）


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    LOG.append(s)


def GET(url, t=240, tries=3):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            return urllib.request.urlopen(req, timeout=t).read()
        except Exception as e:
            last = e
            time.sleep(4 * (k + 1))
    raise last


def getj(url, t=240):
    return json.loads(GET(url, t))


def num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "--", "-", "X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def roc(s):
    s = str(s).strip()
    if len(s) == 7 and s.isdigit():
        return datetime.date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7]))
    if len(s) == 8 and s.isdigit():
        return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return None


STOCK = re.compile(r"^\d{4}$")
BROKERS = ["華南永昌", "第一金", "摩根士丹利", "美商高盛", "高盛", "瑞銀", "法興", "元大", "國泰", "凱基", "富邦", "統一", "群益", "永豐", "台新", "元富", "兆豐", "國票", "中信",
           "宏遠", "康和", "犇亞", "玉山", "日盛", "臺銀", "台銀", "合庫", "新光", "大展", "京華", "福邦", "美林", "星展", "野村", "大和", "港商", "盛寶", "彰銀", "土銀", "陽信", "遠智"]


def issuer(name, under_name=""):
    n = name
    if under_name and n.startswith(under_name):
        n = n[len(under_name):]
    for b in BROKERS:
        if b in n:
            return b
    m = re.match(r"^(.*?)[0-9A-Z]{2}[購售]\d{2}$", n)
    return (m.group(1) if m and m.group(1) else "其他")[:4]


# ---------- Black-Scholes ----------
def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S, K, T, r, v, call):
    d1 = (math.log(S / K) + (r + v * v / 2) * T) / (v * math.sqrt(T))
    d2 = d1 - v * math.sqrt(T)
    if call:
        return S * ncdf(d1) - K * math.exp(-r * T) * ncdf(d2)
    return K * math.exp(-r * T) * ncdf(-d2) - S * ncdf(-d1)


def bs_delta(S, K, T, r, v, call):
    d1 = (math.log(S / K) + (r + v * v / 2) * T) / (v * math.sqrt(T))
    return ncdf(d1) if call else ncdf(d1) - 1


def implied_vol(p, S, K, T, r, call):
    """p＝每股權證價值（權證價÷行使比例）。回傳年化波動率，解不出來回 None。"""
    if p <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    intrinsic = max(0.0, (S - K * math.exp(-r * T)) if call else (K * math.exp(-r * T) - S))
    if p < intrinsic * 0.999:
        return None
    lo, hi = 0.02, 4.0
    if bs_price(S, K, T, r, hi, call) < p or bs_price(S, K, T, r, lo, call) > p:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, r, mid, call) > p:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# ---------- 證交所 ----------
def twse_tables(d):
    """回傳 {'C': rows, 'P': rows}，rows 為 dict。"""
    out = {}
    ds = d.strftime("%Y%m%d")
    for typ in ("0999", "0999P"):
        try:
            j = getj("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=%s&type=%s&response=json" % (ds, typ))
        except Exception as e:
            log("TWSE", ds, typ, "FAIL", e)
            continue
        for t in j.get("tables") or []:
            rows = t.get("data") or []
            title = t.get("title") or ""
            if not rows or "權證" not in title:
                continue
            kind = "P" if "認售" in title else "C" if "認購" in title else None
            if kind is None or "牛" in title.replace("不含牛證", "") or "熊" in title.replace("不含熊證", ""):
                continue
            f = {n: i for i, n in enumerate(t["fields"])}
            need = ("證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "收盤價", "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "標的代號", "標的名稱", "標的收盤價/指數")
            if any(n not in f for n in need):
                log("TWSE fields missing", ds, typ, t["fields"])
                continue
            lst = out.setdefault(kind, [])
            for r in rows:
                lst.append(dict(code=r[f["證券代號"]].strip(), name=r[f["證券名稱"]].strip(), vol=num(r[f["成交股數"]]) or 0, trades=num(r[f["成交筆數"]]) or 0,
                                amt=num(r[f["成交金額"]]) or 0, close=num(r[f["收盤價"]]), bid=num(r[f["最後揭示買價"]]), bq=num(r[f["最後揭示買量"]]),
                                ask=num(r[f["最後揭示賣價"]]), aq=num(r[f["最後揭示賣量"]]), under=r[f["標的代號"]].strip(), uname=r[f["標的名稱"]].strip(),
                                S=num(r[f["標的收盤價/指數"]]), mkt="T", kind=kind))
            log("TWSE", ds, typ, kind, len(rows))
        time.sleep(1.2)
    return out


def twse_terms():
    data = getj("https://openapi.twse.com.tw/v1/opendata/t187ap37_L", 600)
    out = {}
    for r in data:
        code = r.get("權證代號", "").strip()
        cls = r.get("類別", "")
        if not code or cls != "一般型":
            continue
        last = roc(r.get("最後交易日", ""))
        K = num(r.get("最新履約價格(元)/履約指數"))
        ratio = num(r.get("最新標的履約配發數量(每仟單位權證)"))
        if not last or not K or not ratio:
            continue
        rem = re.findall(r"剩餘權證([\d,]+)仟單位", r.get("備註", "") or "")
        units = num(rem[-1]) if rem else num(r.get("發行單位數量(仟單位)"))
        out[code] = dict(K=K, ratio=ratio / 1000.0, last=last, units=units)
    return out


# ---------- 櫃買 ----------
def tpex_daily():
    rows = getj("https://www.tpex.org.tw/openapi/v1/tpex_warrant_daily_quts", 300)
    out = []
    for r in rows:
        k = {kk.strip(): vv for kk, vv in r.items()}
        vkey = next((kk for kk in k if kk.startswith("TradeVol")), None)
        name = k.get("Name", "").strip()
        kind = "P" if "售" in name[-6:] else "C" if "購" in name[-6:] else None
        if kind is None:
            continue
        out.append(dict(code=k.get("Code", "").strip(), name=name, vol=num(k.get(vkey)) or 0 if vkey else 0, trades=num(k.get("No.OfTransactions")) or 0,
                        amt=num(k.get("TradeValue")) or 0, close=num(k.get("Close")), bid=None, bq=None, ask=None, aq=None,
                        under=k.get("UnderlyingStockCode", "").strip(), uname=k.get("UnderlyingStock", "").strip(), S=num(k.get("UnderlyingStockClosePrice")),
                        mkt="O", kind=kind, date=roc(k.get("Date", ""))))
    return out


def tpex_terms():
    rows = getj("https://www.tpex.org.tw/openapi/v1/tpex_warrant_issue", 600)
    out = {}
    for r in rows:
        k = {kk.strip(): vv for kk, vv in r.items()}
        if (k.get("Reset") or "N").strip() not in ("N", ""):
            continue
        if (k.get("CapPrice/Index") or "").strip() or (k.get("FloorPrice/Index") or "").strip():
            continue
        code = k.get("Code", "").strip()
        K = num(k.get("LatestExercisePrice"))
        ratio = num(k.get("Latest ExerciseRatio") or k.get("LatestExerciseRatio"))
        last = roc(k.get("ExpiryDate", ""))
        if not code or not K or not ratio or not last:
            continue
        iss = num(k.get("Accum.Accum.Issuance")) or num(k.get("InitialIssuance"))
        can = num(k.get("Accum.CanceledWarrant")) or 0
        out[code] = dict(K=K, ratio=ratio, last=last, units=(iss - can) if iss else None)
    return out


# ---------- 累積排行 ----------
def agg(rows):
    d = {}
    for r in rows:
        if not STOCK.match(r["under"]):
            continue
        a = d.setdefault(r["under"], [r["uname"], 0, 0, 0, 0])
        if r["kind"] == "C":
            a[1] += int(r["amt"]); a[2] += int(r["trades"])
        else:
            a[3] += int(r["amt"]); a[4] += int(r["trades"])
    return d


def load_hist(path):
    try:
        h = json.load(open(path, encoding="utf-8"))
        h.setdefault("tw", {}); h.setdefault("ot", {})
        return h
    except Exception:
        return {"tw": {}, "ot": {}}


def backfill(hist, today):
    """回補證交所近 NEED_DAYS 個交易日；回傳最新交易日的 tables（供個別權證明細）。"""
    got, latest, d, n = 0, None, today, 0
    while got < NEED_DAYS and n < 45:
        if d.weekday() < 5:
            key = d.isoformat()
            if key in hist["tw"] and latest is not None:
                got += 1
            else:
                tb = twse_tables(d)
                rows = (tb.get("C") or []) + (tb.get("P") or [])
                if rows:
                    hist["tw"][key] = agg(rows)
                    got += 1
                    if latest is None:
                        latest = (d, tb)
                time.sleep(1.2)
            if latest is None and key in hist["tw"]:
                pass
        d -= datetime.timedelta(1)
        n += 1
    return latest


def trim(hist):
    for k in ("tw", "ot"):
        ds = sorted(hist[k].keys(), reverse=True)[:KEEP_DAYS]
        hist[k] = {x: hist[k][x] for x in sorted(ds)}


# ---------- 計算個別權證 ----------
def build_rows(rows, terms, today):
    res, uvol = [], {}
    for r in rows:
        if not STOCK.match(r["under"]):
            continue
        t = terms.get(r["code"])
        if not t or not r["S"]:
            continue
        days = (t["last"] - today).days
        if days < 20:
            continue
        call = r["kind"] == "C"
        bid, ask = r["bid"], r["ask"]
        quoted = bool(bid and ask and bid > 0 and ask > 0 and ask >= bid)
        price = (bid + ask) / 2 if quoted else (r["close"] or None)
        if not price or price < 0.1:
            continue
        S, K, ratio = r["S"], t["K"], t["ratio"]
        T = days / 365.0
        iv = implied_vol(price / ratio, S, K, T, R_FREE, call)
        if iv is None:
            continue
        dl = abs(bs_delta(S, K, T, R_FREE, iv, call))
        lev = dl * S * ratio / price
        mny = ((S - K) / K if call else (K - S) / K) * 100       # 價內外%：正＝價內、負＝價外
        spr = (ask - bid) / price * 100 if quoted else None
        gl = spr / lev if (spr is not None and lev > 0) else None            # 差槓比＝價差比÷有效槓桿
        if lev < 1 or lev > 60 or abs(mny) > 45:
            continue
        res.append([r["code"], r["name"], r["mkt"], r["kind"], issuer(r["name"], r["uname"]), r["under"], S, K, round(ratio, 4), days, round(price, 2),
                    bid, ask, r["bq"], r["aq"], round(r["vol"] / 1000), int(r["amt"]), round(iv * 100, 1), round(dl, 3), round(lev, 2),
                    None if spr is None else round(spr, 2), round(mny, 1), None if gl is None else round(gl, 3), t["units"]])
    return res


def main(outdir="."):
    today = datetime.date.today()
    hp = os.path.join(outdir, "warrant_hist.json")
    hist = load_hist(hp)
    out = {"updated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "r": R_FREE, "w": [], "u": {}, "dt": None, "do": None, "note": []}
    # 1. 證交所：回補＋最新
    try:
        latest = backfill(hist, today)
    except Exception as e:
        log("backfill FAIL", e)
        latest = None
    tw_rows = []
    if latest:
        d, tb = latest
        out["dt"] = d.isoformat()
        tw_rows = (tb.get("C") or []) + (tb.get("P") or [])
        if not tb.get("P"):
            out["note"].append("證交所認售權證資料未取得（只有認購）")
            log("WARN no TWSE puts")
    # 2. 櫃買：只有當日資料，逐日累積
    ot_rows = []
    try:
        ot_rows = tpex_daily()
        dts = [r["date"] for r in ot_rows if r.get("date")]
        if dts:
            od = max(dts)
            hist["ot"][od.isoformat()] = agg(ot_rows)
            out["do"] = od.isoformat()
    except Exception as e:
        log("TPEX daily FAIL", e)
        out["note"].append("櫃買權證資料未取得")
    # 3. 發行條件
    rows_all = []
    try:
        if tw_rows:
            tt = twse_terms()
            rows_all += build_rows(tw_rows, tt, datetime.date.fromisoformat(out["dt"]))
            log("TWSE built", len(rows_all))
    except Exception as e:
        log("TWSE terms FAIL", e)
        out["note"].append("證交所發行條件未取得")
    try:
        if ot_rows and out["do"]:
            n0 = len(rows_all)
            ot = tpex_terms()
            rows_all += build_rows(ot_rows, ot, datetime.date.fromisoformat(out["do"]))
            log("TPEX built", len(rows_all) - n0)
    except Exception as e:
        log("TPEX terms FAIL", e)
    # 4. 各標的 IV 中位數，並限縮每個標的／方向保留的筆數
    byu = {}
    for w in rows_all:
        byu.setdefault((w[5], w[3]), []).append(w)
    keep, uinfo = [], {}
    names = {}
    for r in tw_rows + ot_rows:
        names[r["under"]] = r["uname"]
    for (u, k), ws in byu.items():
        pool = [w for w in ws if w[9] >= 30 and abs(w[21]) <= 25]
        med = round(statistics.median([w[17] for w in pool]), 1) if len(pool) >= 3 else None
        S = ws[0][6]
        ui = uinfo.setdefault(u, [names.get(u, ""), S, None, None, 0, 0])
        ui[2 if k == "C" else 3] = med
        ui[4 if k == "C" else 5] = len(ws)
        tws = sorted([w for w in ws if w[2] == "T" and w[22] is not None and w[9] >= 30], key=lambda w: w[22])[:14]
        ots = sorted([w for w in ws if w[2] == "O" and w[9] >= 30], key=lambda w: -w[16])[:6]
        keep += tws + ots
    out["w"] = keep
    out["u"] = uinfo
    json.dump(out, open(os.path.join(outdir, "warrants.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    trim(hist)
    json.dump(hist, open(hp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log("done warrants", len(keep), "underlyings", len(uinfo), "hist tw days", len(hist["tw"]), "ot days", len(hist["ot"]))
    open(os.path.join(outdir, "warrant_log.txt"), "w", encoding="utf-8").write("\n".join(LOG))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
