#!/usr/bin/env python3
# 由 GitHub Actions 每天執行：抓資料 -> 產生 data.json（只用標準函式庫，不需 pip）
import os, json, math, time, urllib.request, urllib.parse, datetime as dt

TOKEN = os.environ.get("FINMIND_TOKEN", "")
UA = {"User-Agent": "Mozilla/5.0"}
ERR = {}
MIN_VOL = 1000   # 近 20 日「日均成交量」(張) 低於此值的股票會被排除；想放寬或收緊就改這個數字


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


# ---------- HMM（純 Python 實作的 3 狀態高斯隱馬爾可夫模型，不需安裝套件）----------
def _pdf(v, m, s2):
    return max(math.exp(-(v - m) ** 2 / (2 * s2)) / math.sqrt(2 * math.pi * s2), 1e-300)


def _em(x, mu, s2, K, iters=80):
    T = len(x)
    A = [[0.9 if i == j else 0.1 / (K - 1) for j in range(K)] for i in range(K)]
    pi, prev = [1.0 / K] * K, -1e18
    for _ in range(iters):
        B = [[_pdf(x[t], mu[k], s2[k]) for k in range(K)] for t in range(T)]
        a, c = [[0.0] * K for _ in range(T)], [0.0] * T
        for t in range(T):
            for j in range(K):
                a[t][j] = (pi[j] if t == 0 else sum(a[t - 1][i] * A[i][j] for i in range(K))) * B[t][j]
            c[t] = sum(a[t]) or 1e-300
            a[t] = [v / c[t] for v in a[t]]
        ll = sum(math.log(v) for v in c)
        b = [[1.0] * K for _ in range(T)]
        for t in range(T - 2, -1, -1):
            for i in range(K):
                b[t][i] = sum(A[i][j] * B[t + 1][j] * b[t + 1][j] for j in range(K)) / c[t + 1]
        g = []
        for t in range(T):
            r = [a[t][k] * b[t][k] for k in range(K)]
            z = sum(r) or 1e-300
            g.append([v / z for v in r])
        xi = [[0.0] * K for _ in range(K)]
        for t in range(T - 1):
            for i in range(K):
                for j in range(K):
                    xi[i][j] += a[t][i] * A[i][j] * B[t + 1][j] * b[t + 1][j] / c[t + 1]
        gs = [sum(g[t][k] for t in range(T)) + 1e-9 for k in range(K)]
        pi = g[0][:]
        for i in range(K):
            den = sum(xi[i]) or 1e-300
            row = [max(v / den, 1e-6) for v in xi[i]]
            z = sum(row)
            A[i] = [v / z for v in row]
        mu = [sum(g[t][k] * x[t] for t in range(T)) / gs[k] for k in range(K)]
        s2 = [max(sum(g[t][k] * (x[t] - mu[k]) ** 2 for t in range(T)) / gs[k], 1e-7) for k in range(K)]
        if ll - prev < 1e-4:
            break
        prev = ll
    return ll, mu, s2, A, pi


def hmm_state(d, K=3, n=750, show=120):
    """d: [[日期,開,高,低,收,量],...]。回傳目前狀態機率（只用當天以前資料的濾波機率）。"""
    d = d[-(n + 1):]
    x = [max(-0.11, min(0.11, math.log(d[i][4] / d[i - 1][4]))) for i in range(1, len(d))]
    if len(x) < 150:
        raise RuntimeError("資料不足 150 天")
    dates = [r[0] for r in d[1:]]
    m = sum(x) / len(x)
    var = sum((v - m) ** 2 for v in x) / len(x)
    xs, best = sorted(x), None
    for mult in ((0.5, 1, 2), (1, 1, 1)):
        r = _em(x, [xs[int(len(x) * (k + .5) / K)] for k in range(K)], [var * q for q in mult], K)
        if best is None or r[0] > best[0]:
            best = r
    ll, mu, s2, A, pi = best
    o = sorted(range(K), key=lambda k: s2[k])          # 依日波動由低到高（日報酬的均值很難分辨，波動才是可靠的區分）
    mu, s2, pi = [mu[k] for k in o], [s2[k] for k in o], [pi[k] for k in o]
    A = [[A[i][j] for j in o] for i in o]
    p, prev = [], None
    for t, v in enumerate(x):
        al = [(pi[j] if t == 0 else sum(prev[i] * A[i][j] for i in range(K))) * _pdf(v, mu[j], s2[j]) for j in range(K)]
        z = sum(al) or 1e-300
        prev = [u / z for u in al]
        p.append(prev)
    now = p[-1]
    ni = max(range(K), key=lambda k: now[k])
    streak = 0
    for q in reversed(p):
        if max(range(K), key=lambda k: q[k]) != ni:
            break
        streak += 1
    return {"lab": ["低波動", "中波動", "高波動"], "mu": [round(v * 100, 3) for v in mu],
            "sd": [round(math.sqrt(v) * 100, 3) for v in s2],
            "dur": [round(min(999, 1 / max(1 - A[i][i], 1e-3)), 1) for i in range(K)],
            "now": [round(v, 3) for v in now], "now_i": ni, "streak": streak,
            "next": [round(sum(now[i] * A[i][j] for i in range(K)), 3) for j in range(K)],
            "d": dates[-show:], "p": [[round(v, 3) for v in q] for q in p[-show:]], "n": len(x)}


def stock(sid, start):
    o = {}
    px = fm("TaiwanStockPrice", sid, start)
    o["d"] = [[r["date"], r["open"], r["max"], r["min"], r["close"], round(r["Trading_Volume"] / 1000)]
              for r in px if r["close"] > 0 and r["open"] > 0]
    v20 = [r[5] for r in o["d"][-20:]]
    o["avg20"] = round(sum(v20) / len(v20)) if v20 else 0
    if o["avg20"] < MIN_VOL:
        return o   # 量不足：不再抓法人、融資與 HMM，節省額度
    try:
        o["hmm"] = hmm_state(o["d"])
    except Exception as e:
        ERR[sid + "_hmm"] = str(e)
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
    raw = [l.split("#")[0].strip() for l in open("pool.txt", encoding="utf-8")]
    raw = [p for p in raw if p]
    concepts = json.load(open("concepts.json", encoding="utf-8")) if os.path.exists("concepts.json") else {}
    info = {}
    try:
        for r in fm("TaiwanStockInfo"):
            info[r["stock_id"]] = r
    except Exception as e:
        ERR["info"] = str(e)
    n2i = {}   # 股票名稱 -> 代號（優先 4 碼數字的一般股票）
    for sid, r in sorted(info.items(), key=lambda kv: (len(kv[0]) == 4 and kv[0].isdigit(), kv[0])):
        n2i[str(r["stock_name"]).lower()] = sid
    bad = []

    def res(x):
        x = x.strip()
        i = x if x.isdigit() else n2i.get(x.lower())
        if not i: bad.append(x)
        return i
    pool = []
    for x in raw:
        i = res(x)
        if i and i not in pool: pool.append(i)
    conc = {k: [i for i in (res(x) for x in v) if i] for k, v in concepts.items()}
    focus = [l.split("#")[0].strip() for l in open("focus.txt", encoding="utf-8")] if os.path.exists("focus.txt") else []
    focus = [f for f in focus if f]
    if focus:                         # 只留 focus.txt 列出的族群，股票池 = 這些族群的成員
        bad += ["（族群不存在）" + f for f in focus if f not in conc]
        conc = {k: v for k, v in conc.items() if k in focus}
        pool = []
        for v in conc.values():
            for i in v:
                if i not in pool: pool.append(i)
    old = {}
    try:
        old = json.load(open("data.json", encoding="utf-8")).get("stocks", {})
    except Exception:
        pass
    snap = snapshot()
    ind = {}
    for sid, r in info.items():
        if sid in snap and len(sid) == 4 and sid.isdigit() and not sid.startswith("0"):
            ind.setdefault(r["industry_category"], []).append(sid)
    start = (dt.date.today() - dt.timedelta(days=900)).isoformat()
    stocks, limited, dropped = {}, False, []
    for sid in pool:
        if limited:                      # 已達流量上限：其餘沿用上次資料
            if sid in old: stocks[sid] = old[sid]
            continue
        try:
            s = stock(sid, start)
            s["name"] = info.get(sid, {}).get("stock_name") or snap.get(sid, {}).get("n", sid)
            s["ind"] = info.get(sid, {}).get("industry_category", "")
            if s["avg20"] < MIN_VOL:
                dropped.append({"c": sid, "n": s["name"], "v": s["avg20"]})
                continue
            stocks[sid] = s
            time.sleep(0.15)
        except Exception as e:
            ERR[sid] = str(e)
            if any(k in str(e).lower() for k in ("limit", "reach", "402")):
                limited = True
                ERR["limit"] = "FinMind 流量達上限，其餘股票沿用上次資料；請設定 FINMIND_TOKEN"
            if sid in old: stocks[sid] = old[sid]
    out = {"updated": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
           "groups": {"ind": pack(ind, snap), "con": pack(conc, snap)}, "stocks": stocks,
           "unresolved": sorted(set(bad)), "dropped": dropped, "min_vol": MIN_VOL, "focus": focus, "errors": ERR}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("股票池", len(pool), "檔，保留", len(stocks), "檔，量不足排除", len(dropped), "檔；找不到名稱：", sorted(set(bad)) or "無", "；錯誤：", ERR or "無")


if __name__ == "__main__":
    main()
