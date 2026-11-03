"""
SWING LAB - five paper accounts that trade the researched strategies by themselves. Paper money only, no real orders.

  acd        A/C/D intraday: A = gap + opening drive, C = 15-min opening-range breakout on the day after an NR7 day,
             D = wild-open fade. First signal of the day only. Buys a weekly option about 100 pts in the money.
  c          Option C: P1 pullback in a strong trend (1 day), P2 bounce in a correction (3 days), P3 turn of the month.
             Monthly call spread: buy the call 300 below spot, sell the call 300 above.
  cplus      Option C+: like C, but P1 and P2 use a WEEKLY 300/300 spread (expiry at least 2 days after the exit).
  c_acd      Option C and A/C/D sharing one account.
  cplus_acd  Option C+ and A/C/D sharing one account.

Rules are frozen copies of the backtests in Desktop/Nifty-research (swing/*.py, obt/buy_signals.py, r6/cplus.py).
Each account starts with CFG["start_cash"]. Its own files live in spread_data/swing/ (saved to the data repo like the rest).

Hooks in nify_1.py: init(globals()), feed_keys(), register_routes(app), inject(html). Everything runs in its own threads,
and every error is caught, so the main app carries on whatever happens here.
"""
import os, json, csv, time, threading, queue, math, shutil
from datetime import datetime, timedelta, timezone, date
from urllib.parse import quote as urlq

try:
    import requests
except Exception:                       # pragma: no cover
    requests = None
try:
    import pandas as pd
except Exception:                       # pragma: no cover
    pd = None

IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api.upstox.com"

CFG = dict(
    start_cash=100000.0,     # each account starts with this
    short_margin=25000.0,    # extra money a broker blocks for the sold leg of one spread lot (approx.)
    size_frac=0.30,          # swing: premium per trade = 30% of the account
    size_max_frac=0.45,      # ...but at least 1 lot when one lot's premium is at most 45% of the account
    acd_per_lot=100000.0,    # A/C/D: 1 lot per Rs 1 lakh of account
    stop_pct=2.0,            # swing: exit when Nifty falls this much below the entry level
    spread_pts=300,          # swing spread strikes: spot -300 (buy) and spot +300 (sell)
    acd_itm=100,             # A/C/D: option 100 pts in the money
    liquid_rate=0.06,        # idle cash earns this a year (a liquid fund), as in the backtest
    fresh_s=15.0,            # a quote older than this is not used for fills
    swing_from="09:16", swing_until="09:45",   # swing entries/exits happen at the open (first minute skipped for sane quotes)
    acd_late_s=180,          # an A/C/D entry more than 3 minutes late is skipped
    eod_at="15:31", preview_at="15:36")

ACCTS = [("acd", "A/C/D", {"ACD"}), ("c", "Option C", {"SW_M"}), ("cplus", "Option C+", {"SW_CP"}),
         ("c_acd", "Option C + A/C/D", {"SW_M", "ACD"}), ("cplus_acd", "Option C+ + A/C/D", {"SW_CP", "ACD"})]
ANAME = {a: n for a, n, _ in ACCTS}
BACKTEST = {   # reference results from the research (Rs 1 lakh, compounded) - shown beside the live numbers
    "acd": dict(month=3.6, period="Oct 2024 - Oct 2026, real option prices", dd=-20, note="about 7 trades a month; 43% winners; +9.1 pts a trade after costs"),
    "c": dict(month=2.4, period="since 2013 (+2.9% a month since 2021)", dd=-47, note="about 20 trades a year; losing years 2016 and 2021"),
    "cplus": dict(month=3.7, period="since 2013 (+4.4% a month since 2021)", dd=-63, note="weekly spreads for the 1-3 day signals; before 2019 weekly options did not exist"),
    "c_acd": dict(month=5.3, period="Oct 2024 - Oct 2026", dd=-37, note="the two halves often offset each other"),
    "cplus_acd": dict(month=8.1, period="Oct 2024 - Oct 2026", dd=-44, note="two good years; expect less over the long run"),
}
RULE_NAME = {"P1": "Pullback in a strong trend", "P2": "Bounce in a correction", "P3": "Turn of the month",
             "A": "A: gap + opening drive", "C": "C: breakout after an NR7 day", "D": "D: wild-open fade"}
TRADE_COLS = ["acct", "intent", "rule", "kind", "struct", "label", "lots", "qty", "entry_time", "exit_time", "entry_spot", "exit_spot",
              "entry_pts", "exit_pts", "gross", "fees", "net", "reason", "blocked", "days_held", "notes"]
EQ_COLS = ["day", "acct", "book", "mtm", "cash", "interest", "open_positions"]

G = {}; S = None; LOCK = threading.RLock(); READY = False; DIR = ""
JOBS = queue.Queue(); DATA = dict(daily=[], daily_day=None, hol=set(), hol_ok=False, hol_day=None, fail=0.0, msg="")
EVENTS = []          # recent events for the page


# ----------------------------------------------------------------------------- helpers
def g(n, d=None): return G.get(n, d)
def _now(): return datetime.now(IST)
def _ts(nw=None): return (nw or _now()).strftime("%Y-%m-%d %H:%M:%S")
def _hm(s): h, m = s.split(":"); return int(h) * 60 + int(m)
def _mins(nw): return nw.hour * 60 + nw.minute
def _p(n): return os.path.join(DIR, n)
def _f(x):
    try: v = float(x); return v if v == v else None
    except Exception: return None
def _r(x, n=2):
    v = _f(x); return None if v is None else round(v, n)

def _atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh: json.dump(obj, fh, default=str)
    os.replace(tmp, path)

def _append(path, cols, row):
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        if new: w.writeheader()
        w.writerow({c: ("" if row.get(c) is None else row.get(c)) for c in cols})

def _read(path):
    try:
        with open(path, newline="", encoding="utf-8") as fh: return [r for r in csv.DictReader(fh) if None not in r.values()]
    except Exception: return []

def _event(msg, kind="info"):
    row = dict(t=_ts(), kind=kind, msg=msg)
    EVENTS.append(row); del EVENTS[:-200]
    try:
        with open(_p("swing_events.log"), "a", encoding="utf-8") as fh: fh.write(f"{row['t']}  {kind.upper():5s} {msg}\n")
    except Exception: pass
    if kind in ("trade", "error"):
        try: g("say", lambda *_: None)("Swing: " + msg)
        except Exception: pass

def _err(where, e):
    msg = f"{where}: {e!r}"[:300]; print("[swinglab]", msg, flush=True)
    if S is not None: S["last_err"] = f"{_ts()} {msg}"
    _event(msg, "error")

def _save():
    try: _atomic(_p("swing_state.json"), S)
    except Exception as e: print("[swinglab] save failed", e, flush=True)

def _get(path, timeout=30):
    if requests is None: raise RuntimeError("requests not available")
    r = requests.get(API + path, headers=g("HDR"), timeout=timeout)
    if r.status_code >= 400: raise RuntimeError(f"HTTP {r.status_code} {r.text[:120]}")
    return r.json()

def _quote(key):
    with g("lock"): return dict((g("quotes") or {}).get(key) or {})

def _fresh(q): return bool(q) and q.get("ltp") is not None and q.get("t") and time.time() - q["t"] <= CFG["fresh_s"]

def _spot():
    q = _quote(g("UNDERLYING_KEY")); return q.get("ltp") if _fresh(q) else None


# ----------------------------------------------------------------------------- state
def _new_acct():
    c = CFG["start_cash"]
    return dict(cash=c, start=c, interest=0.0, realized=0.0, fees=0.0, n=0, wins=0, gw=0.0, gl=0.0, eq_hist=[], last_int_day=str(date.today()))

def _new_state():
    return dict(v=1, enabled=True, created=_ts(), accts={a: _new_acct() for a, _, _ in ACCTS}, intents={}, seq=0, plan=None, preview=None,
                done={}, acd=None, d1m={}, last_err=None)

def init(globals_dict):
    global G, S, DIR, READY
    G = globals_dict
    DIR = os.path.join(g("DATA_DIR"), "swing"); os.makedirs(DIR, exist_ok=True)
    with LOCK:
        S = _new_state()
        try:
            if os.path.exists(_p("swing_state.json")):
                S.update(json.load(open(_p("swing_state.json"), encoding="utf-8")))
                for a, _, _ in ACCTS: S["accts"].setdefault(a, _new_acct())
                S.setdefault("d1m", {})
        except Exception as e: _err("load state", e)
        try: EVENTS[:] = []
        except Exception: pass
        READY = True; _save()
    threading.Thread(target=_worker, name="swing-worker", daemon=True).start()
    threading.Thread(target=_loop, name="swing-loop", daemon=True).start()
    _event("Swing Lab ready: " + ", ".join(n for _, n, _ in ACCTS))

def feed_keys():
    """Option legs the lab needs live quotes for (the app subscribes to these)."""
    try:
        if not READY or not S: return []
        return [l["key"] for it in S["intents"].values() if it["status"] == "OPEN" for l in it["legs"]]
    except Exception: return []


# ----------------------------------------------------------------------------- trading calendar
def _is_trading(d):
    return d.weekday() < 5 and str(d) not in DATA["hol"]

def _next_trading(d, n=1):
    x = d
    while n > 0:
        x += timedelta(days=1)
        if _is_trading(x): n -= 1
    return x

def _prev_trading(d):
    x = d - timedelta(days=1)
    while not _is_trading(x): x -= timedelta(days=1)
    return x

def _left_in_month(d):
    """How many trading days are left in d's month, counting d itself."""
    n, x = 0, d
    while x.month == d.month:
        if _is_trading(x): n += 1
        x += timedelta(days=1)
    return n

def _nth_of_next_month(d, nth=4):
    x = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
    k = 0
    while True:
        if _is_trading(x):
            k += 1
            if k == nth: return x
        x += timedelta(days=1)


# ----------------------------------------------------------------------------- market data
def _fetch_daily():
    """Nifty daily candles (official open/high/low/close), oldest first, completed days only."""
    k = urlq(g("UNDERLYING_KEY"), safe=""); to = date.today(); rows = {}
    for a, b in ((to - timedelta(days=1300), to - timedelta(days=650)), (to - timedelta(days=651), to)):
        d = _get(f"/v3/historical-candle/{k}/days/1/{b}/{a}")
        for c in (d.get("data") or {}).get("candles") or []: rows[c[0][:10]] = (c[0][:10], float(c[1]), float(c[2]), float(c[3]), float(c[4]))
    out = [rows[x] for x in sorted(rows) if x < str(to)]
    if len(out) < 600: raise RuntimeError(f"only {len(out)} daily candles")
    return out

def _fetch_holidays():
    d = _get("/v2/market/holidays"); hs = set()
    for x in d.get("data") or []:
        typ = str(x.get("holiday_type", "")).upper(); closed = [str(e).upper() for e in (x.get("closed_exchanges") or [])]
        if "TRADING" in typ and ("NSE" in closed or "NFO" in closed or not closed): hs.add(str(x.get("date"))[:10])
    return hs

def _fetch_1m_days():
    """Per-day facts from 1-minute index candles for the last ~5 weeks (as the research built them from 1-minute data):
    day -> [r30 = 09:15-09:44 range / open, high, low, last close (15:29)]."""
    k = urlq(g("UNDERLYING_KEY"), safe=""); to = date.today() - timedelta(days=1); bars = {}
    for a, b in ((to - timedelta(days=38), to - timedelta(days=20)), (to - timedelta(days=19), to)):
        try:
            d = _get(f"/v3/historical-candle/{k}/minutes/1/{b}/{a}")
            for c in (d.get("data") or {}).get("candles") or []:
                t = c[0]; day, hmv = t[:10], t[11:16]
                if "09:15" <= hmv <= "15:29": bars.setdefault(day, {})[hmv] = (float(c[1]), float(c[2]), float(c[3]), float(c[4]))
        except Exception as e: _err("1-min history for A/C/D", e)
    return {day: _day_facts([b[h] for h in sorted(b)], "09:15" in b) for day, b in bars.items() if len(b) >= 300}

def _day_facts(bl, starts_0915=True):
    """bl: the day's 1-minute bars in time order [o,h,l,c] -> [r30, high, low, close]."""
    r30 = (max(x[1] for x in bl[:30]) - min(x[2] for x in bl[:30])) / bl[0][0] if starts_0915 and len(bl) >= 30 else None
    return [r30, max(x[1] for x in bl), min(x[2] for x in bl), bl[-1][3]]

def _ensure_data():
    today = str(date.today())
    if DATA["hol_day"] != today:
        try: DATA["hol"] = _fetch_holidays(); DATA["hol_ok"] = True
        except Exception as e: DATA["hol_ok"] = False; _err("market holidays (weekends only are used)", e)
        DATA["hol_day"] = today
    if DATA["daily_day"] != today:
        DATA["daily"] = _fetch_daily(); DATA["daily_day"] = today
        _event(f"loaded {len(DATA['daily'])} daily candles (last {DATA['daily'][-1][0]})")
    with LOCK:
        have = [d for d in S["d1m"] if d < today]
        need = len(have) < 15 or str(_prev_trading(date.today())) not in S["d1m"]
    if need:
        got = _fetch_1m_days()
        with LOCK: S["d1m"].update({d: v for d, v in got.items() if d < today}); S["d1m"] = dict(sorted(S["d1m"].items())[-45:]); _save()

def _today_bars():
    """Completed 1-minute bars of the index today: list indexed by minute since 09:15 -> [o,h,l,c] (missing minutes filled)."""
    U, SH = g("UNDERLYING_KEY"), int(g("SHIFT", 19800) or 19800)
    with g("clock"): d = dict((g("CANDLES") or {}).get(U, {}))
    now = time.time(); day0 = (int(now) + SH) // 86400; got = {}
    for m, c in d.items():
        if (m + SH) // 86400 != day0 or m + 60 > now: continue
        k = ((m + SH) % 86400 - 33300) // 60
        if 0 <= k < 375: got[k] = c
    if 0 not in got: return []
    out = []; last = None
    for k in range(0, max(got) + 1):
        c = got.get(k)
        if c is None: c = [last[3]] * 4
        out.append([float(c[0]), float(c[1]), float(c[2]), float(c[3])]); last = out[-1]
    return out


# ----------------------------------------------------------------------------- swing signals (P1, P2) on daily candles
def swing_flags(rows):
    """rows: completed daily candles, oldest first. Returns the P1/P2 signals on the LAST row (same maths as swing/mine_open.py)."""
    df = pd.DataFrame(rows, columns=["day", "open", "high", "low", "close"]); c = df.close
    rn = (c / c.shift(10) - 1) * 100; q = rn.rolling(500, min_periods=250).rank(pct=True)
    ibs = (df.close - df.low) / (df.high - df.low)
    hi250 = c.rolling(250).max(); gap = (df.open / c.shift(1) - 1) * 100
    L = len(df) - 1
    v = dict(day=df.day.iloc[L], close=float(c.iloc[L]), ret10=_r(rn.iloc[L]), ret10_rank=_r(q.iloc[L], 3), close_pos=_r(ibs.iloc[L], 3),
             below_52w_high=_r((1 - c.iloc[L] / hi250.iloc[L]) * 100), gap=_r(gap.iloc[L]), up_day=bool(df.close.iloc[L] >= df.open.iloc[L]))
    v["P1"] = bool(q.iloc[L] > 0.8 and ibs.iloc[L] < 0.2)
    v["P2"] = bool(c.iloc[L] <= hi250.iloc[L] * 0.90 and gap.iloc[L] > 0.3 and df.close.iloc[L] < df.open.iloc[L])
    return v

def _plan_for(day, rows, source):
    """The swing plan for trading day `day` given completed daily candles before it."""
    plan = dict(day=str(day), source=source, made=_ts(), entries=[], exits=[], flags=None, note="")
    if not _is_trading(day): plan["note"] = "not a trading day"; return plan
    if rows and str(_prev_trading(day)) <= rows[-1][0] < str(day):
        fl = swing_flags(rows); plan["flags"] = fl
        for rule in ("P1", "P2"):
            if fl[rule]: plan["entries"].append(dict(rule=rule, exit_due=str(_next_trading(day, 1 if rule == "P1" else 3)), why=_why(rule, fl)))
    else: plan["note"] = "daily data does not end on the previous trading day; P1/P2 not checked"
    if _left_in_month(day) == 4:
        plan["entries"].append(dict(rule="P3", exit_due=str(_nth_of_next_month(day, 4)), why=f"{day} is the 4th-last trading day of the month"))
    with LOCK:
        for e in list(plan["entries"]):       # a rule already holding a position past this day does not stack another
            busy = [it for it in S["intents"].values() if it["status"] == "OPEN" and it["rule"] == e["rule"] and it.get("exit_due", "") > str(day)]
            if busy: e["skip"] = "still holding this rule's previous trade"
        plan["exits"] = [it["id"] for it in S["intents"].values() if it["status"] == "OPEN" and it["kind"] == "swing" and it.get("exit_due", "9") <= str(day)]
    plan["p3_next"] = _next_p3(day)
    return plan

def _next_p3(day):
    x = day
    for _ in range(45):
        if _is_trading(x) and _left_in_month(x) == 4: return str(x)
        x += timedelta(days=1)
    return None

def _why(rule, fl):
    if rule == "P1": return f"10-day rise {fl['ret10']:+.1f}% is in the top 20% of the last 2 years (rank {fl['ret10_rank']:.2f}) and Nifty closed in the bottom 20% of the day's range ({fl['close_pos']:.2f})"
    return f"Nifty is {fl['below_52w_high']:.1f}% below its 52-week high, opened {fl['gap']:+.2f}% higher and closed lower"


# ----------------------------------------------------------------------------- options: expiries, chain, prices, fees
def _expiries():
    try: return sorted(e for e in set(g("opts").exp) if e >= date.today())
    except Exception: return list(g("exp_choices", lambda: [])())

def _monthly_after(d):
    E = _expiries(); last = {}
    for e in E: last[(e.year, e.month)] = max(last.get((e.year, e.month), e), e)
    for e in sorted(last.values()):
        if e >= d: return e
    return None

def _any_after(d):
    for e in _expiries():
        if e >= d: return e
    return None

def _chain(expiry):
    d = _get(f"/v2/option/chain?instrument_key={urlq(g('UNDERLYING_KEY'), safe='')}&expiry_date={expiry}", timeout=20)
    return d.get("data") or []

def _leg(rows, typ, strike):
    side = "call_options" if typ == "CE" else "put_options"; best = None
    for x in rows:
        o = x.get(side) or {}
        if not o.get("instrument_key") or x.get("strike_price") is None: continue
        dist = abs(float(x["strike_price"]) - strike)
        if best is None or dist < best[0]: best = (dist, float(x["strike_price"]), o)
    if best is None or best[0] > 60: raise RuntimeError(f"no {typ} near {strike}")
    md = best[2].get("market_data") or {}
    return dict(key=best[2]["instrument_key"], strike=best[1], typ=typ, bid=_f(md.get("bid_price")), ask=_f(md.get("ask_price")), ltp=_f(md.get("ltp")))

def _buy_px(q):
    a = _f(q.get("ask")); return a if a and a > 0 else (_f(q.get("ltp")) or 0) + 0.5
def _sell_px(q):
    b = _f(q.get("bid")); return b if b and b > 0 else max((_f(q.get("ltp")) or 0) - 0.5, 0.05)

def _fees(orders):
    try: return float(g("charges")(orders))
    except Exception:
        t = sum(p * q for _, p, q in orders); return 20 * len(orders) * 1.18 + t * 0.0005

def _live_legs(it):
    """Current quotes for an intent's legs: from the live feed when fresh, else from the option chain."""
    out = []; need = []
    for l in it["legs"]:
        q = _quote(l["key"])
        if _fresh(q): out.append(q)
        else: out.append(None); need.append(l)
    if need:
        rows = {}
        for l in need:
            ex = l["expiry"]
            if ex not in rows: rows[ex] = _chain(ex)
        for i, l in enumerate(it["legs"]):
            if out[i] is None:
                x = _leg(rows[l["expiry"]], l["typ"], l["strike"]); out[i] = dict(bid=x["bid"], ask=x["ask"], ltp=x["ltp"])
    return out


# ----------------------------------------------------------------------------- accounts
def _equity(a):
    """Book equity: free cash + money tied up in open positions at cost."""
    A = S["accts"][a]
    return A["cash"] + sum(p["blocked"] for it in S["intents"].values() if it["status"] == "OPEN" for aa, p in it["pos"].items() if aa == a)

def _accts_for(tag):
    return [a for a, _, tags in ACCTS if tag in tags]

def _interest():
    today = str(date.today())
    for a, A in S["accts"].items():
        last = A.get("last_int_day") or today
        days = (date.fromisoformat(today) - date.fromisoformat(last)).days
        if days > 0 and A["cash"] > 0:
            add = A["cash"] * ((1 + CFG["liquid_rate"]) ** (days / 365) - 1); A["cash"] += add; A["interest"] += add
        A["last_int_day"] = today


# ----------------------------------------------------------------------------- opening and closing
def _open_swing(rule, exit_due, why):
    """Open the swing trade for `rule` in every account that trades it: monthly spread (C) and, for P1/P2, weekly spread (C+)."""
    spot = _spot()
    if not spot: raise RuntimeError("no fresh Nifty price")
    due = date.fromisoformat(exit_due); W = CFG["spread_pts"]
    k1, k2 = round((spot - W) / 50) * 50, round((spot + W) / 50) * 50
    structs = [("M", _monthly_after(due + timedelta(days=7)), ["SW_M", "SW_CP"] if rule == "P3" else ["SW_M"])]
    if rule in ("P1", "P2"): structs.append(("W", _any_after(due + timedelta(days=2)), ["SW_CP"]))
    for st, ex, tags in structs:
        try:
            if ex is None: raise RuntimeError("no suitable expiry in the instrument list")
            rows = _chain(ex); L, Sh = _leg(rows, "CE", k1), _leg(rows, "CE", k2)
            debit = _buy_px(L) - _sell_px(Sh)
            if debit <= 0: raise RuntimeError(f"bad spread price {debit}")
            accts = [a for t in tags for a in _accts_for(t)]
            _open_intent(kind="swing", rule=rule, struct=st, legs=[dict(L, side=1, expiry=str(ex), px=_buy_px(L)), dict(Sh, side=-1, expiry=str(ex), px=_sell_px(Sh))],
                         unit_pts=debit, accts=accts, spot=spot, exit_due=exit_due, why=why,
                         label=f"{ex:%d %b} {L['strike']:.0f}/{Sh['strike']:.0f} CE spread ({'monthly' if st == 'M' else 'weekly'})")
        except Exception as e: _err(f"open {rule} {st}", e)

def _open_intent(kind, rule, struct, legs, unit_pts, accts, spot, label, why, exit_due=None, side=1, extra=None):
    lot = int(g("LOT", 65) or 65)
    with LOCK:
        S["seq"] += 1; iid = f"S{_now():%Y%m%d}-{S['seq']:03d}"
        it = dict(id=iid, kind=kind, rule=rule, struct=struct, legs=legs, unit_pts=_r(unit_pts), spot_in=_r(spot), side=side, label=label, why=why,
                  t_in=_ts(), day_in=str(date.today()), exit_due=exit_due, stop_spot=_r(spot * (1 - CFG["stop_pct"] / 100)) if kind == "swing" else None,
                  status="OPEN", pos={}, skipped={}, mark=None)
        if extra: it.update(extra)
        for a in accts:
            A = S["accts"][a]; eq = _equity(a); unit = unit_pts * lot
            if kind == "swing":
                blk1 = unit + CFG["short_margin"]
                lots = max(int(eq * CFG["size_frac"] // unit), 1 if unit <= CFG["size_max_frac"] * eq else 0); lots = min(lots, int(A["cash"] // blk1), 200)
                blocked = lots * blk1
            else:
                lots = min(max(1, int(eq // CFG["acd_per_lot"])), int(A["cash"] // unit), 200); blocked = lots * unit
            if lots < 1: it["skipped"][a] = "not enough free money"; continue
            qty = lots * lot
            orders = [("B" if l["side"] > 0 else "S", l["px"], qty) for l in legs]
            fee = _fees(orders)
            A["cash"] -= blocked + fee; A["fees"] += fee
            it["pos"][a] = dict(lots=lots, qty=qty, blocked=round(blocked, 2), fee_in=round(fee, 2))
        if not it["pos"]:
            _event(f"{RULE_NAME.get(rule, rule)}: no account could afford {label}", "warn"); return None
        S["intents"][iid] = it; _save()
    _event(f"BOUGHT {RULE_NAME.get(rule, rule)} - {label} at {unit_pts:.2f} pts (Nifty {spot:,.1f}) for " +
           ", ".join(f"{ANAME[a]} {p['lots']} lot" for a, p in it["pos"].items()), "trade")
    return iid

def _close_intent(iid, reason):
    with LOCK:
        it = S["intents"].get(iid)
        if not it or it["status"] not in ("OPEN", "QUEUED"): return
        it["status"] = "CLOSING"
    try:
        qs = _live_legs(it); spot = _spot() or _f((it.get("mark") or {}).get("spot")) or it["spot_in"]
        exit_px = [(_sell_px(q) if l["side"] > 0 else _buy_px(q)) for l, q in zip(it["legs"], qs)]
        value = sum(l["side"] * p for l, p in zip(it["legs"], exit_px))          # what the position is worth per unit now
        with LOCK:
            for a, p in it["pos"].items():
                A = S["accts"][a]; qty = p["qty"]
                fee = _fees([("S" if l["side"] > 0 else "B", px, qty) for l, px in zip(it["legs"], exit_px)])
                gross = (value - it["unit_pts"]) * qty
                net = gross - fee - p["fee_in"]
                A["cash"] += p["blocked"] + gross - fee; A["fees"] += fee; A["realized"] += net; A["n"] += 1
                if net > 0: A["wins"] += 1; A["gw"] += net
                else: A["gl"] += -net
                _append(_p("swing_trades.csv"), TRADE_COLS, dict(acct=a, intent=iid, rule=it["rule"], kind=it["kind"], struct=it["struct"], label=it["label"],
                        lots=p["lots"], qty=qty, entry_time=it["t_in"], exit_time=_ts(), entry_spot=it["spot_in"], exit_spot=_r(spot), entry_pts=it["unit_pts"],
                        exit_pts=_r(value), gross=_r(gross), fees=_r(fee + p["fee_in"]), net=_r(net), reason=reason, blocked=_r(p["blocked"]),
                        days_held=(date.today() - date.fromisoformat(it["day_in"])).days, notes=it.get("why", "")[:160]))
            it.update(status="CLOSED", t_out=_ts(), exit_value=_r(value), spot_out=_r(spot), reason=reason, exit_px=[_r(x) for x in exit_px])
            S["closed"] = (S.get("closed") or [])[-300:] + [dict((k, it[k]) for k in ("id", "kind", "rule", "struct", "label", "t_in", "t_out", "unit_pts", "exit_value", "spot_in", "spot_out", "reason"))]
            del S["intents"][iid]; _save()
        _event(f"SOLD {RULE_NAME.get(it['rule'], it['rule'])} - {it['label']}: {it['unit_pts']:.2f} -> {value:.2f} pts ({reason})", "trade")
    except Exception as e:
        with LOCK:
            if iid in S["intents"]: S["intents"][iid].update(status="OPEN", retry_at=time.time() + 20)
        _err(f"close {iid}", e)


# ----------------------------------------------------------------------------- A/C/D intraday
def _atr5(bars, k):
    n = (k // 5) * 5
    if n < 10: return None
    hh = [max(b[1] for b in bars[i:i + 5]) for i in range(0, n, 5)][-6:]
    ll = [min(b[2] for b in bars[i:i + 5]) for i in range(0, n, 5)][-6:]
    return sum(h - l for h, l in zip(hh, ll)) / len(hh)

def _acd_day_setup():
    """Facts A/C/D need that are known before the open: previous close, NR7 yesterday, opening-range history.
    Built from 1-minute data like the research (prev close = 15:29 close, ranges = 1-minute high/low); daily candles are the fallback."""
    today = str(date.today()); prev = str(_prev_trading(date.today()))
    d1 = [(d, v) for d, v in sorted(S["d1m"].items()) if d < today]
    if d1 and d1[-1][0] >= prev and len(d1) >= 7:          # >= : a special session (e.g. a Sunday budget day) counts as the previous day
        prev_close = d1[-1][1][3]; rng = [v[1] - v[2] for _, v in d1[-7:]]; src = "1-minute data"
    else:
        rows = DATA["daily"]
        if not rows or rows[-1][0] < prev: return None
        prev_close = rows[-1][4]; rng = [r[2] - r[3] for r in rows[-7:]]; src = "daily candles (1-minute history missing)"
    r30 = [v[0] for _, v in d1 if v[0] is not None][-20:]
    return dict(day=today, prev_close=prev_close, prev_nr7=bool(rng[-1] <= min(rng) + 1e-9), r30_hist=r30, c_checked=[], fired=None, src=src,
                status="waiting for 09:15", A=None, C=None, D=None, done=False)

def _acd_step(nw):
    """Run once a second in market hours: find the first A/C/D signal of the day, enter, and manage the exit."""
    A = S.get("acd")
    if not A or A.get("day") != str(date.today()):
        if not DATA["daily"]: return
        A = _acd_day_setup(); S["acd"] = A
        if A is None: return
    bars = _today_bars()
    if not bars: A["status"] = "no 1-minute bars yet"; return
    kd = len(bars) - 1                                # last completed bar
    o0 = bars[0][0]; gap = (o0 / A["prev_close"] - 1) * 100; A["gap"] = round(gap, 2); A["open"] = o0
    if A["fired"] is None and not A["done"]:
        cands = []
        if kd >= 29 and A["A"] is None:
            mv = (bars[29][3] / o0 - 1) * 100; s = 1 if mv > 0.3 else -1 if mv < -0.3 else 0
            A["A"] = dict(move=round(mv, 2), side=s if s and s == (1 if gap > 0 else -1 if gap < 0 else 0) else 0)
            if A["A"]["side"]: cands.append(("A", 29, A["A"]["side"]))
        if A["prev_nr7"] and kd >= 19:
            hi, lo = max(b[1] for b in bars[:15]), min(b[2] for b in bars[:15])
            for k in range(19, kd + 1, 5):
                if k in A["c_checked"]: continue
                A["c_checked"].append(k); s = 1 if bars[k][3] > hi else -1 if bars[k][3] < lo else 0
                if s and A["C"] is None: A["C"] = dict(k=k, side=s, hi=hi, lo=lo); cands.append(("C", k, s))
        if kd >= 44 and A["D"] is None:
            h30, l30 = max(b[1] for b in bars[:30]), min(b[2] for b in bars[:30]); r30 = (h30 - l30) / o0
            hist = A["r30_hist"]; avg = sum(hist) / len(hist) if hist else None
            ok = len(hist) >= 10 and r30 >= 1.5 * avg
            tp = sum((b[1] + b[2] + b[3]) / 3 for b in bars[:45]) / 45
            A["D"] = dict(r30=round(r30 * 100, 3), avg=round(avg * 100, 3) if avg else None, side=(-1 if bars[44][3] > tp else 1) if ok else 0)
            if A["D"]["side"]: cands.append(("D", 44, A["D"]["side"]))
        if cands:
            name, k, s = sorted(cands, key=lambda c: (c[1], "ACD".index(c[0])))[0]
            start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=IST) + timedelta(minutes=9 * 60 + 15 + k + 1)
            late = (nw - start).total_seconds()
            A["fired"] = dict(rule=name, k=k, side=s, at=f"{start:%H:%M}")
            if late > CFG["acd_late_s"]: A["status"] = f"{name} fired at {start:%H:%M} but the app saw it {late:.0f}s late: skipped"; A["done"] = True
            else:
                atr = _atr5(bars, k + 1)
                JOBS.put(("acd_open", dict(rule=name, k=k, side=s, atr=atr)))
                A["status"] = f"{RULE_NAME[name]} fired - {'buying a call' if s > 0 else 'buying a put'}"
        elif kd >= 60 and not (A["prev_nr7"] and A["C"] is None and kd < 370):
            A["status"] = "no A/C/D signal today"
        else: A["status"] = "watching"

def _acd_open(rule, k, side, atr):
    spot = _spot()
    if not spot: raise RuntimeError("no fresh Nifty price")
    ex = _any_after(date.today())
    if ex is None: raise RuntimeError("no expiry")
    atm = round(spot / 50) * 50; typ = "CE" if side > 0 else "PE"
    strike = atm - CFG["acd_itm"] if side > 0 else atm + CFG["acd_itm"]
    L = _leg(_chain(ex), typ, strike); px = _buy_px(L)
    a = atr if atr else spot * 0.0011
    extra = dict(exit_rule="HOLD60" if rule == "D" else "EOD_TRAIL", e_spot=spot, trail=2 * a, stop_lvl=-2 * a, best=0.0,
                 hold_until=(_now() + timedelta(minutes=60)).strftime("%H:%M:%S"))
    _open_intent(kind="acd", rule=rule, struct="ITM", legs=[dict(L, side=1, expiry=str(ex), px=px)], unit_pts=px, accts=_accts_for("ACD"), spot=spot,
                 label=f"{ex:%d %b} {L['strike']:.0f} {typ} ({'call' if side > 0 else 'put'}, ~{CFG['acd_itm']} in the money)", why=RULE_NAME[rule], side=side, extra=extra)
    with LOCK:
        if S.get("acd"): S["acd"]["done"] = True


# ----------------------------------------------------------------------------- the loops
def _worker():
    while True:
        job, arg = JOBS.get()
        try:
            if job == "open_swing": _open_swing(**arg)
            elif job == "close": _close_intent(**arg)
            elif job == "acd_open": _acd_open(**arg)
            elif job == "data": _ensure_data()
        except Exception as e: _err(job, e)

def _mark(nw):
    """Update live marks of open positions from the feed (cheap, no API calls)."""
    spot = _spot()
    for it in list(S["intents"].values()):
        qs = [_quote(l["key"]) for l in it["legs"]]
        if all(_fresh(q) for q in qs):
            val = sum(l["side"] * (_sell_px(q) if l["side"] > 0 else _buy_px(q)) for l, q in zip(it["legs"], qs))
            it["mark"] = dict(value=_r(val), t=_ts(nw), spot=_r(spot))
        elif spot: it.setdefault("mark", {}); it["mark"] = dict(it["mark"] or {}, spot=_r(spot))

def _exit_checks(nw):
    spot = _spot(); hmv = nw.strftime("%H:%M")
    for it in list(S["intents"].values()):
        if it["status"] != "OPEN" or time.time() < it.get("retry_at", 0): continue
        if it["kind"] == "swing":
            if it.get("exit_due_hit") == str(date.today()):
                JOBS.put(("close", dict(iid=it["id"], reason="planned exit (retry)"))); it["status"] = "QUEUED"; continue
            if spot and it.get("stop_spot") and spot <= it["stop_spot"]:
                JOBS.put(("close", dict(iid=it["id"], reason=f"2% stop (Nifty {spot:,.1f})"))); it["status"] = "QUEUED"
        else:
            if it["day_in"] != str(date.today()) or hmv >= "15:20":
                JOBS.put(("close", dict(iid=it["id"], reason="15:20 close"))); it["status"] = "QUEUED"; continue
            if it.get("exit_rule") == "HOLD60":
                if nw.strftime("%H:%M:%S") >= it["hold_until"]: JOBS.put(("close", dict(iid=it["id"], reason="60 minutes"))); it["status"] = "QUEUED"
            elif spot:
                adv = (spot - it["e_spot"]) * it["side"]
                if adv <= it["stop_lvl"]:
                    JOBS.put(("close", dict(iid=it["id"], reason=("trailing stop" if it["stop_lvl"] > -it["trail"] else "stop") + f" ({adv:+.1f} pts)"))); it["status"] = "QUEUED"
                else:
                    it["best"] = max(it["best"], adv); it["stop_lvl"] = max(it["stop_lvl"], it["best"] - it["trail"])

def _eod(nw):
    today = str(date.today())
    for a, A in S["accts"].items():
        book = _equity(a); mtm = book + _unreal(a)
        A["eq_hist"] = [x for x in A["eq_hist"] if x[0] != today] + [[today, round(book), round(mtm)]]
        _append(_p("swing_equity.csv"), EQ_COLS, dict(day=today, acct=a, book=round(book), mtm=round(mtm), cash=round(A["cash"]), interest=round(A["interest"]),
                                                     open_positions=sum(1 for it in S["intents"].values() if a in it["pos"])))
    bars = _today_bars()
    if len(bars) >= 300:
        S["d1m"][today] = _day_facts(bars); S["d1m"] = dict(sorted(S["d1m"].items())[-45:])

def _unreal(a):
    tot = 0.0
    for it in S["intents"].values():
        p = it["pos"].get(a); m = it.get("mark") or {}
        if p and m.get("value") is not None: tot += (m["value"] - it["unit_pts"]) * p["qty"]
    return tot

def _loop():
    time.sleep(20); last_data = 0.0
    while True:
        try:
            nw = _now(); today = date.today(); ts = str(today); mn = _mins(nw)
            if S["enabled"] and time.time() - last_data > 300 and (DATA["daily_day"] != ts or DATA["hol_day"] != ts) and mn >= _hm("08:35"):
                last_data = time.time(); JOBS.put(("data", {}))
            with LOCK:
                _interest()
                if S["enabled"] and DATA["daily_day"] == ts and (not S.get("plan") or S["plan"].get("day") != ts or S["plan"].get("source") != "official"):
                    S["plan"] = _plan_for(today, DATA["daily"], "official"); _save()
                    if S["plan"]["entries"]: _event("today's swing plan: " + ", ".join(e["rule"] + (" (skip: " + e["skip"] + ")" if e.get("skip") else "") for e in S["plan"]["entries"]))
                trading = _is_trading(today)
                if trading and _hm("09:15") <= mn < _hm("15:30"):
                    _mark(nw)
                    if S["enabled"]:
                        _exit_checks(nw)
                        if _hm(CFG["swing_from"]) <= mn < _hm(CFG["swing_until"]) and S["done"].get("swing") != ts and _spot():
                            S["done"]["swing"] = ts
                            for it in list(S["intents"].values()):
                                if it["kind"] == "swing" and it["status"] == "OPEN" and it.get("exit_due", "9") <= ts:
                                    JOBS.put(("close", dict(iid=it["id"], reason="planned exit at the open"))); it["status"] = "QUEUED"
                                    it["exit_due_hit"] = ts
                            pl = S.get("plan")
                            if pl and pl.get("day") == ts:
                                for e in pl["entries"]:
                                    if not e.get("skip"): JOBS.put(("open_swing", dict(rule=e["rule"], exit_due=e["exit_due"], why=e["why"])))
                            elif pl is None or pl.get("day") != ts: _event("no swing plan for today (daily data missing) - swing entries skipped", "warn")
                        if mn >= _hm(CFG["swing_until"]) and S["done"].get("swing") != ts:
                            S["done"]["swing"] = ts; _event("the app was not running at the open: today's swing entries were skipped", "warn")
                            for it in list(S["intents"].values()):
                                if it["kind"] == "swing" and it["status"] == "OPEN" and it.get("exit_due", "9") <= ts:
                                    JOBS.put(("close", dict(iid=it["id"], reason="planned exit (late)"))); it["status"] = "QUEUED"
                        if mn < _hm("15:20"): _acd_step(nw)
                if trading and mn >= _hm(CFG["eod_at"]) and S["done"].get("eod") != ts:
                    S["done"]["eod"] = ts; _eod(nw); _save()
                if trading and mn >= _hm(CFG["preview_at"]) and DATA["daily"] and (not S.get("preview") or S["preview"].get("made", "")[:10] != ts):
                    bars = _today_bars()
                    if len(bars) > 300:
                        rows = DATA["daily"] + [(ts, bars[0][0], max(b[1] for b in bars), min(b[2] for b in bars), bars[-1][3])]
                        S["preview"] = _plan_for(_next_trading(today), rows, "preview from today's live candle"); _save()
                        if S["preview"]["entries"]: _event("tomorrow's swing preview: " + ", ".join(e["rule"] for e in S["preview"]["entries"]))
        except Exception as e: _err("loop", e)
        time.sleep(1.0)


# ----------------------------------------------------------------------------- page data and commands
def _acct_view(a, all_trades):
    A = S["accts"][a]; book = _equity(a); un = _unreal(a); mtm = book + un
    hist = A["eq_hist"] + [[str(date.today()), round(book), round(mtm)]]
    peak, dd = A["start"], 0.0
    for _, _, v in hist: peak = max(peak, v); dd = min(dd, (v / peak - 1) * 100)
    months = {}
    prev = A["start"]
    for d, b, m in hist:
        months.setdefault(d[:7], dict(month=d[:7], start=prev, end=m)); months[d[:7]]["end"] = m
    ms = sorted(months.values(), key=lambda x: x["month"])
    for i, x in enumerate(ms):
        x["start"] = ms[i - 1]["end"] if i else A["start"]; x["ret"] = round((x["end"] / x["start"] - 1) * 100, 2) if x["start"] else None
    trades = [r for r in all_trades if r.get("acct") == a]
    for x in ms:
        tt = [t for t in trades if t["exit_time"][:7] == x["month"]]; x["trades"] = len(tt); x["wins"] = sum(1 for t in tt if float(t["net"] or 0) > 0)
        x["pnl"] = round(sum(float(t["net"] or 0) for t in tt))
    td = [t for t in trades if t["exit_time"][:10] == str(date.today())]
    opens = []
    for it in S["intents"].values():
        p = it["pos"].get(a)
        if not p: continue
        m = it.get("mark") or {}
        opens.append(dict(id=it["id"], rule=it["rule"], rule_name=RULE_NAME.get(it["rule"]), kind=it["kind"], label=it["label"], lots=p["lots"], qty=p["qty"],
                          blocked=p["blocked"], entry=it["unit_pts"], now=m.get("value"), unreal=round((m["value"] - it["unit_pts"]) * p["qty"]) if m.get("value") is not None else None,
                          spot_in=it["spot_in"], spot=m.get("spot"), stop=it.get("stop_spot"), exit_due=it.get("exit_due"), t_in=it["t_in"], status=it["status"],
                          exit_rule=it.get("exit_rule"), stop_lvl=_r(it.get("stop_lvl"), 1), best=_r(it.get("best"), 1), hold_until=it.get("hold_until"), why=it.get("why")))
    n = A["n"]
    return dict(id=a, name=ANAME[a], start=A["start"], book=round(book), mtm=round(mtm), unreal=round(un), cash=round(A["cash"]), interest=round(A["interest"]),
                realized=round(A["realized"]), fees=round(A["fees"]), n=n, wins=A["wins"], win_rate=round(A["wins"] / n * 100) if n else None,
                pf=round(A["gw"] / A["gl"], 2) if A["gl"] > 0 else None, ret=round((mtm / A["start"] - 1) * 100, 2), maxdd=round(dd, 1),
                month_ret=ms[-1]["ret"] if ms else None, today=round(sum(float(t["net"] or 0) for t in td)), today_n=len(td),
                hist=hist[-800:], months=ms[-36:], trades=list(reversed(trades[-150:])), open=opens, backtest=BACKTEST[a])

def snapshot():
    if not READY: return dict(ready=False)
    with LOCK:
        acd = S.get("acd") or {}; tr = _read(_p("swing_trades.csv"))
        return dict(ready=True, enabled=S["enabled"], created=S["created"], cfg=CFG, accts=[_acct_view(a, tr) for a, _, _ in ACCTS],
                    plan=S.get("plan"), preview=S.get("preview"), acd=dict((k, acd.get(k)) for k in ("day", "status", "gap", "prev_nr7", "A", "C", "D", "fired", "done", "prev_close", "src")) if acd else None,
                    d1m_days=len(S["d1m"]), data=dict(daily_day=DATA["daily_day"], daily_last=DATA["daily"][-1][0] if DATA["daily"] else None, holidays_ok=DATA["hol_ok"]),
                    events=list(reversed(EVENTS[-60:])), err=S.get("last_err"), now=_ts(), lot=g("LOT"))

def _command(j):
    cmd = j.get("cmd")
    with LOCK:
        if cmd == "enable": S["enabled"] = bool(j.get("on")); _save(); _event("auto-trading " + ("ON" if S["enabled"] else "PAUSED")); return True, ""
        if cmd == "close":
            it = S["intents"].get(j.get("id"))
            if not it or it["status"] != "OPEN": return False, "no such open trade"
            it["status"] = "QUEUED"; JOBS.put(("close", dict(iid=it["id"], reason="closed by hand"))); return True, ""
        if cmd == "reset":
            if S["intents"]: return False, "close the open trades first"
            stamp = _now().strftime("%Y%m%d_%H%M%S"); arch = _p(f"archive_{stamp}"); os.makedirs(arch, exist_ok=True)
            for f in ("swing_state.json", "swing_trades.csv", "swing_equity.csv", "swing_events.log"):
                if os.path.exists(_p(f)): shutil.move(_p(f), os.path.join(arch, f))
            d1m = S["d1m"]; S.clear(); S.update(_new_state()); S["d1m"] = d1m; _save(); _event("fresh accounts started (old data archived)"); return True, ""
    return False, "unknown command"

def register_routes(app):
    from flask import jsonify, request, Response
    @app.route("/api/swing/state", endpoint="swing_state")
    def _state():
        try: return jsonify(snapshot())
        except Exception as e: return jsonify(ready=False, error=str(e)), 500
    @app.route("/api/swing/cmd", methods=["POST"], endpoint="swing_cmd")
    def _cmd():
        ok, msg = _command(request.json or {})
        return jsonify(ok=True) if ok else (jsonify(ok=False, error=msg), 400)
    @app.route("/api/swing/<name>.csv", endpoint="swing_csv")
    def _csv(name):
        f = {"trades": "swing_trades.csv", "equity": "swing_equity.csv"}.get(name)
        if not f: return Response("not found", 404)
        data = open(_p(f), "rb").read() if READY and os.path.exists(_p(f)) else b""
        return Response(data, mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={f}"})


# ----------------------------------------------------------------------------- the page
from swinglab_ui import CSS as SW_CSS, PAGE as SW_PAGE, JS as SW_JS     # noqa: E402  (kept in its own file to keep this one readable)

def inject(html):
    """Add the Swing tab to the dashboard. If any anchor is missing the page is left as it was."""
    try:
        out = html
        def rep(old, new):
            nonlocal out
            if out.count(old) != 1: raise ValueError("anchor not found exactly once: " + old[:48])
            out = out.replace(old, new)
        rep("</style></head><body>", SW_CSS + "</style></head><body>")
        rep('<div class="page" id="vSettings">', SW_PAGE + '<div class="page" id="vSettings">')
        rep("['lab','🤖 Auto Lab'],", "['lab','🤖 Auto Lab'],['swing','🌊 Swing'],")
        rep("if(v==='lab')labLoad();", "if(v==='lab')labLoad();if(v==='swing')swLoad();")
        rep("['position','signals','score','lab','trades','settings'].includes(prev)", "['position','signals','score','lab','swing','trades','settings'].includes(prev)")
        rep("</script></body></html>", SW_JS + "</script></body></html>")
        return out
    except Exception as e:
        print("[swinglab] the Swing tab was NOT added:", e, flush=True)
        return html
