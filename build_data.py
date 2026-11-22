#!/usr/bin/env python3
# 由 GitHub Actions 每天執行：抓資料 -> 產生 data.json（只用標準函式庫，不需 pip）
import os, io, csv, json, math, time, statistics, urllib.request, urllib.parse, datetime as dt

TOKEN = os.environ.get("FINMIND_TOKEN", "")
UA = {"User-Agent": "Mozilla/5.0"}
ERR = {}
BODY_MAX = 0.015   # 「小紅小黑」：K 棒實體占開盤價的比例上限
FUND_PER_RUN = 30  # 每次最多更新幾檔的營收／借券／除權息（其餘沿用舊資料），避免超過流量
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


# ---------- 還原股價、週線潛伏與日線條件 ----------
def shares_out():
    out = {}
    for url in ("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"):
        try:
            for r in get(url):
                code = r.get("公司代號") or r.get("SecuritiesCompanyCode")
                v = next((num(x) for k, x in r.items() if "已發行普通股" in k), None)
                if code and v: out[str(code).strip()] = v
        except Exception as e:
            ERR["shares"] = ERR.get("shares", "") + f" {e}"
    return out


def get_fund(sid, start):
    """營收、借券賣出餘額、除權息：變動慢，每週更新一次即可"""
    f = {"t": dt.date.today().isoformat(), "rev": None, "sbl": None, "div": None, "fo": None}
    try:
        f["rev"] = [[int(r["revenue_year"]), int(r["revenue_month"]), r["revenue"]] for r in fm("TaiwanStockMonthRevenue", sid, start)]
    except Exception as e:
        ERR[sid + "_rev"] = str(e)
    try:
        s60 = (dt.date.today() - dt.timedelta(days=60)).isoformat()
        f["sbl"] = [[r["date"], r.get("SBLShortSalesCurrentDayBalance")] for r in fm("TaiwanDailyShortSaleBalances", sid, s60)]
    except Exception as e:
        ERR[sid + "_sbl"] = str(e)
    try:
        f["fo"] = [[r["date"], r.get("ForeignInvestmentSharesRatio")] for r in
                   fm("TaiwanStockShareholding", sid, (dt.date.today() - dt.timedelta(days=150)).isoformat())]
    except Exception as e:
        ERR[sid + "_fo"] = str(e)
    try:
        f["div"] = [[r["date"], r["before_price"], r["after_price"]] for r in fm("TaiwanStockDividendResult", sid, start)
                    if r.get("before_price") and r.get("after_price")]
    except Exception as e:
        ERR[sid + "_div"] = str(e)
    return f


def adjust(d, divs):
    """用除權息前收盤與參考價的比率，把除權息日之前的價格往下還原"""
    ev = sorted([(x[0], x[2] / x[1]) for x in (divs or []) if x[1] and 0.5 < x[2] / x[1] < 1.05], reverse=True)
    out, f, j = [list(r) for r in d], 1.0, 0
    for i in range(len(out) - 1, -1, -1):
        while j < len(ev) and ev[j][0] > out[i][0]:
            f *= ev[j][1]
            j += 1
        for k in (1, 2, 3, 4):
            out[i][k] *= f
    return out


def screen(da, inst, fund, sh):
    n = len(da)
    if n < 130: return None
    o, h, c, v = [[r[i] for r in da] for i in (1, 2, 4, 5)]
    ma = lambda a, k: sum(a[-k:]) / k
    ma20, ma60, ma120 = ma(c, 20), ma(c, 60), ma(c, 120)
    sd = statistics.pstdev(c[-20:])
    bw = [4 * statistics.pstdev(c[i - 19:i + 1]) / (sum(c[i - 19:i + 1]) / 20) for i in range(n - 120, n)]
    r = {"c1": ma(v, 5) < ma(v, 60) and sum(abs(c[-k] / o[-k] - 1) <= BODY_MAX for k in (1, 2, 3)) >= 2,
         "c2": any(abs(c[-1] / m - 1) <= 0.04 for m in (ma60, ma120)) or ((ma20 - 2 * sd) * 0.98 <= c[-1] <= ma20),
         "c11": bw[-1] <= min(bw) * 1.05, "bw": round(bw[-1] * 100, 1)}
    mc = {}
    for x in da: mc[x[0][:7]] = x[4]
    mc = list(mc.values())
    r["c13"] = (sum(mc[-20:]) / sum(mc[-21:-1]) - 1 >= -0.005) if len(mc) >= 21 else None   # 月線(20 個月)中軌向上或走平
    wk = {}
    for x in da:
        y = dt.date.fromisoformat(x[0]).isocalendar()
        wk.setdefault((y[0], y[1]), []).append(x)
    W = [[g[0][1], g[-1][4], sum(q[5] for q in g), len(g)] for g in (wk[k] for k in sorted(wk))]   # 週開、週收、週量、天數
    r["c3"] = None
    if len(W) >= 6:      # c3 為真＝「沒有」出現攻擊失敗（量增紅K後下週跌回 1/2 且量縮）
        a, b = W[-2], W[-1]
        r["c3"] = not (a[1] > a[0] and a[2] > 1.5 * sum(q[2] for q in W[-6:-2]) / 4 and b[3] >= 3
                       and b[1] < (a[0] + a[1]) / 2 and b[2] < a[2])
    rv = {(y, m): val for y, m, val in (fund.get("rev") or [])}
    yoy = [rv[k] / rv[(k[0] - 1, k[1])] - 1 for k in sorted(rv)[-3:] if rv.get((k[0] - 1, k[1]))]
    r["yoy"] = [round(q * 100, 1) for q in yoy]
    r["c5"] = (len(yoy) == 3 and yoy[0] < yoy[1] < yoy[2]) if fund.get("rev") else None
    sb = [x[1] for x in (fund.get("sbl") or []) if x[1] is not None]
    r["c12"] = (sb[-1] <= sb[-6] <= sb[-11] and sb[-1] < sb[-11]) if len(sb) >= 11 else None
    neck = max(h[-23:-3])
    r["dist"] = round((neck / c[-1] - 1) * 100, 1)
    r["d7"] = bool(c[-1] > o[-1] and v[-1] > 1.3 * sum(v[-21:-1]) / 20 and sum(c[-k] < c[-k - 1] for k in (2, 3, 4)) >= 2 and -1 <= r["dist"] <= 3)
    r["d14"] = bool(c[-1] >= max(c[-20:]) and c[-1] > ma120)
    ok = bool(inst) and inst[-1][0] == da[-1][0]      # 投信資料日期要和股價同一天才採用
    t = [x[2] for x in inst] if ok else []
    r["tb3"] = round(sum(t[-3:])) if len(t) >= 3 else None
    r["tb1"] = round(t[-1] * 1000 / sh * 100, 2) if t and sh else None
    r["d15"] = (r["tb3"] > 0 and r["tb1"] > 0.2) if r["tb3"] is not None and r["tb1"] is not None else None
    r["ma"] = [round(ma20, 2), round(ma60, 2), round(ma120, 2)]
    return r


def parse_tdcc(raw, codes):
    """集保戶股權分散表：分級 15 = 1,000 張以上；12–15 = 400 張以上；1–3 = 10 張以下"""
    lv, dates = {}, {}
    for row in csv.reader(io.StringIO(raw)):
        if len(row) >= 6 and row[1].strip() in codes and row[2].strip().isdigit():
            c = row[1].strip()
            lv.setdefault(c, {})[int(row[2])] = num(row[5]) or 0
            dates[c] = f"{row[0][:4]}-{row[0][4:6]}-{row[0][6:8]}"
    return {c: [dates[c], round(v.get(15, 0), 2), round(sum(v.get(i, 0) for i in (12, 13, 14, 15)), 2),
                round(sum(v.get(i, 0) for i in (1, 2, 3)), 2)] for c, v in lv.items()}


def tdcc_all(codes):
    try:
        req = urllib.request.Request("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", headers=UA)
        return parse_tdcc(urllib.request.urlopen(req, timeout=180).read().decode("utf-8-sig"), codes)
    except Exception as e:
        ERR["tdcc"] = str(e)
        return {}


def ref30(sym, last_vol):
    """30 分K（Yahoo，估計值）：MA12/MA45、乖離、MA45 方向、扣抵價、近期帶量紅K低點"""
    r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=30m&range=60d")["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    bars = [(t, q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i] or 0)
            for i, t in enumerate(r["timestamp"]) if q["close"][i] is not None and q["open"][i] is not None]
    if len(bars) < 60: return None
    c = [b[4] for b in bars]
    ma = lambda k, e=0: sum(c[len(c) - k - e:len(c) - e]) / k
    m12, m45, m45p = ma(12), ma(45), ma(45, 1)
    avgv = lambda i: sum(b[5] for b in bars[max(0, i - 20):i]) / max(1, min(20, i))
    red = next((bars[i] for i in range(len(bars) - 1, 20, -1) if bars[i][4] > bars[i][1] and bars[i][5] > 1.5 * avgv(i)), None)
    tz = dt.timezone(dt.timedelta(hours=8))
    f = lambda t: dt.datetime.fromtimestamp(t, tz).strftime("%m-%d %H:%M")
    return {"m12": round(m12, 2), "m45": round(m45, 2), "up45": m45 > m45p, "bias": round((m12 / m45 - 1) * 100, 2),
            "ded": round(c[-45], 2), "last": round(c[-1], 2), "red": round(red[3], 2) if red else None,
            "redt": f(red[0]) if red else None, "asof": f(bars[-1][0])}


def stock(sid, start, fundfn, sh, sym):
    o = {}
    px = fm("TaiwanStockPrice", sid, start)
    o["d"] = [[r["date"], r["open"], r["max"], r["min"], r["close"], round(r["Trading_Volume"] / 1000)]
              for r in px if r["close"] > 0 and r["open"] > 0]
    v20 = [r[5] for r in o["d"][-20:]]
    o["avg20"] = round(sum(v20) / len(v20)) if v20 else 0
    o["sh"] = round(sh / 1000) if sh else None
    o["tn"] = [[r["date"], r.get("Trading_turnover")] for r in px if r["close"] > 0 and r["open"] > 0][-60:]
    if o["avg20"] < MIN_VOL:
        return o   # 量不足：不再抓其他資料，節省額度
    try:
        m = {}
        for r in fm("TaiwanStockInstitutionalInvestorsBuySell", sid, start):
            k = {"Foreign_Investor": 1, "Investment_Trust": 2, "Dealer_self": 3, "Dealer_Hedging": 3}.get(r["name"])
            if k:
                m.setdefault(r["date"], [0, 0, 0])[k - 1] += (r["buy"] - r["sell"]) / 1000
        o["inst"] = [[d, round(v[0]), round(v[1]), round(v[2])] for d, v in sorted(m.items())][-150:]
    except Exception as e:
        ERR[sid + "_inst"] = str(e)
    try:
        o["mg"] = [[r["date"], r["MarginPurchaseTodayBalance"],
                    r["MarginPurchaseTodayBalance"] - r["MarginPurchaseYesterdayBalance"], r.get("ShortSaleTodayBalance"), r.get("MarginPurchaseLimit")]
                   for r in fm("TaiwanStockMarginPurchaseShortSale", sid, start)][-150:]
    except Exception as e:
        ERR[sid + "_mg"] = str(e)
    try:      # 當沖率＝當沖成交股數 ÷ 總成交股數（散戶短線熱度）
        vm = {r["date"]: r["Trading_Volume"] for r in px}
        o["dt"] = [[r["date"], round(r["Volume"] / vm[r["date"]] * 100, 1)] for r in
                   fm("TaiwanStockDayTrading", sid, (dt.date.today() - dt.timedelta(days=90)).isoformat())
                   if r.get("Volume") and vm.get(r["date"])][-60:]
    except Exception as e:
        ERR[sid + "_dt"] = str(e)
    try:
        o["rf"] = ref30(sym, o["d"][-1][5])
    except Exception as e:
        ERR[sid + "_rf"] = str(e)
    fund = fundfn()
    o["fund"] = fund
    da = adjust(o["d"], fund.get("div"))      # HMM 與篩選都用還原價
    o["adj"] = bool(fund.get("div"))
    try:
        o["hmm"] = hmm_state(da)
    except Exception as e:
        ERR[sid + "_hmm"] = str(e)
    try:
        o["sc"] = screen(da, o.get("inst"), fund, sh)
    except Exception as e:
        ERR[sid + "_sc"] = str(e)
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
    shares = shares_out()
    ind = {}
    for sid, r in info.items():
        if sid in snap and len(sid) == 4 and sid.isdigit() and not sid.startswith("0"):
            ind.setdefault(r["industry_category"], []).append(sid)
    start = (dt.date.today() - dt.timedelta(days=900)).isoformat()
    budget = [FUND_PER_RUN if TOKEN else 0]     # 沒有 token 就不抓營收／借券／除權息

    def mk_fund(sid):
        def f():
            fu = (old.get(sid) or {}).get("fund") or {}
            try: age = (dt.date.today() - dt.date.fromisoformat(fu.get("t", "2000-01-01"))).days
            except Exception: age = 999
            if (not fu or age > 6) and budget[0] > 0:
                budget[0] -= 1
                fu = get_fund(sid, start)
            return fu
        return f
    stocks, limited, dropped = {}, False, []
    for sid in pool:
        if limited:                      # 已達流量上限：其餘沿用上次資料
            if sid in old: stocks[sid] = old[sid]
            continue
        try:
            s = stock(sid, start, mk_fund(sid), shares.get(sid), f"{sid}.TWO" if info.get(sid, {}).get("type") == "tpex" else f"{sid}.TW")
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
    td = tdcc_all(set(stocks))        # 集保每週公布一次；歷史靠每次更新時累積
    for sid, s in stocks.items():
        hist = list((old.get(sid) or {}).get("tdcc") or [])
        if sid in td and td[sid][0] not in [x[0] for x in hist]: hist.append(td[sid])
        s["tdcc"] = sorted(hist)[-60:]
    cg = pack(conc, snap)
    gp = {g["name"]: g["avg"] for g in cg}
    for sid, s in stocks.items():            # 同族群強勢：取該股所屬族群中平均漲幅最高者
        best = None
        for gname, ids in conc.items():
            if sid in ids and gname in gp and (best is None or gp[gname] > best[1]): best = (gname, gp[gname])
        if s.get("sc") is not None: s["sc"]["grp"] = {"n": best[0], "avg": best[1]} if best else None
    out = {"updated": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
           "groups": {"ind": pack(ind, snap), "con": cg}, "stocks": stocks,
           "unresolved": sorted(set(bad)), "dropped": dropped, "min_vol": MIN_VOL, "focus": focus, "errors": ERR}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("股票池", len(pool), "檔，保留", len(stocks), "檔，量不足排除", len(dropped), "檔；找不到名稱：", sorted(set(bad)) or "無", "；錯誤：", ERR or "無")


if __name__ == "__main__":
    main()
