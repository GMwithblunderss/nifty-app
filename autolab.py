"""
AUTO LAB - a separate paper-trading account that trades 1 lot by itself when chosen signals fire.

  * Own account, own P&L, own trades, own data folder (spread_data/lab/). Nothing here touches your other trades.
  * Never places a real order. Prices come from the live Upstox quotes the app already receives.
  * What it trades (frozen rules, see RULES): when rvol BULL, orbv BULL or dtrend BEAR turns on, it BUYS one lot of an
    option about 100 pts in the money (call on BULL, put on BEAR). It exits when Nifty futures move +40 pts (target) or
    -25 pts (stop) from entry, after 30 minutes, or at 15:20. One trade at a time, no daily limit.
  * Every signal that fires is logged (even when it is skipped, with the reason) so the data can be analysed later.

Hooks in nify_1.py are tiny: init(), safe_hook(), feed_keys(), register_routes(), inject(). If anything here fails the
main app carries on.
"""
import os, json, csv, time, threading, shutil
from datetime import datetime, timedelta, timezone

try:
    import requests
except Exception:                      # pragma: no cover
    requests = None

IST = timezone(timedelta(hours=5, minutes=30))

# ----------------------------------------------------------------------------- the rules (frozen for the test)
RULES = dict(
    target_pts=40.0, stop_pts=25.0, max_hold_s=1800,          # exit on Nifty FUTURES move from entry, or time
    entry_from="09:20", entry_until="15:00", force_exit="15:20",
    itm_pts=100, min_dte=2,                                    # option strike distance; skip expiries closer than this many days
    safety_max_entries_per_day=40,                             # only a runaway-bug guard, not a trading limit
    fresh_quote_s=10.0, warmup_s=900, mark_every_s=5.0)
SIGNALS = [("rvol", "BULL"), ("orbv", "BULL"), ("dtrend", "BEAR")]   # (signal key in the app, direction traded)

TRADE_COLS = ["trade_id", "day", "signal_key", "signal_name", "direction", "fire_time_ist", "fire_ts", "fill_time_ist", "fill_latency_s",
              "expiry", "dte", "strike", "opt_type", "instrument_key", "qty", "lot_size",
              "entry_bid", "entry_ask", "entry_ltp", "entry_px", "entry_spread_pct", "entry_delta", "entry_iv", "entry_oi", "entry_volume",
              "entry_spot", "entry_fut", "fut_at_fire", "target_fut", "stop_fut", "all_states_at_fire", "verdict_at_fire",
              "exit_time_ist", "exit_ts", "exit_reason", "hold_s", "exit_bid", "exit_ask", "exit_ltp", "exit_px", "exit_px_src", "exit_spot", "exit_fut",
              "fut_move_signed", "fut_mfe", "fut_mae", "t_mfe_s", "t_mae_s", "opt_bid_max", "opt_bid_min",
              "gross_pnl", "charges", "net_pnl", "net_pct_of_premium", "equity_after", "flags"]
SIG_COLS = ["signal_event_id", "time_ist", "ts", "signal_key", "signal_name", "direction", "prev_state", "action", "trade_id", "spot", "fut", "verdict", "all_states"]
MARK_COLS = ["trade_id", "ts", "time_ist", "fut", "spot", "opt_bid", "opt_ask", "opt_ltp", "fut_move", "unreal_net"]

G = {}                      # the main app's globals (live), set by init()
LOCK = threading.RLock()
S = None                    # persistent state (account, switches, open trade)
PREV = {}                   # last seen state of every signal (not saved: after a restart the first look only records)
PENDING = {"sid": None, "t": 0.0}
RECENT, SIGLOG = [], []     # in-memory copies of the latest rows for the page
READY = False
LAB_DIR = ""


# ----------------------------------------------------------------------------- small helpers
def g(name, default=None):
    return G.get(name, default)

def _now_s(nw=None):
    return (nw or datetime.now(IST)).strftime("%Y-%m-%d %H:%M:%S")

def _hm(s):
    h, m = s.split(":"); return int(h) * 60 + int(m)

def _r(x, n=2):
    try: return None if x is None else round(float(x), n)
    except Exception: return None

def _f(x):
    try:
        v = float(x); return v if v == v else None
    except Exception: return None

def _p(name):
    return os.path.join(LAB_DIR, name)

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

def _read_tail(path, n):
    try:
        with open(path, newline="", encoding="utf-8") as fh: rows = [r for r in csv.DictReader(fh) if None not in r.values()]
        return rows[-n:]
    except Exception: return []

def _log(msg):
    try:
        with open(_p("lab_events.log"), "a", encoding="utf-8") as fh: fh.write(f"{_now_s()}  {msg}\n")
    except Exception: pass

def _say(msg):
    try: g("say", lambda *_: None)(msg)
    except Exception: pass

def _err(where, e):
    msg = f"{where}: {e!r}"
    print("[autolab]", msg, flush=True)
    if S is not None: S["last_err"] = f"{_now_s()} {msg}"
    _log("ERROR " + msg)

def _new_state():
    eq = float(g("START_EQUITY", 500000) or 500000)
    return dict(v=1, enabled=True, sigs={f"{k}:{d}": True for k, d in SIGNALS}, start=eq, realized=0.0, peak=eq, maxdd=0.0, n=0, wins=0,
                gross_win=0.0, gross_loss=0.0, seq=0, pos=None, day=None, day_n=0, day_pnl=0.0, created=_now_s(), last_err=None, fired={})

def _save():
    try: _atomic(_p("lab_state.json"), S)
    except Exception as e: _err("save", e)

def _fresh(q, wall):
    return bool(q) and q.get("ltp") is not None and q.get("t") and (wall - q["t"]) <= RULES["fresh_quote_s"]


# ----------------------------------------------------------------------------- start-up
def init(globals_dict):
    """Called once from main() after the app has loaded its instruments and token."""
    global G, S, LAB_DIR, READY
    G = globals_dict
    LAB_DIR = os.path.join(g("DATA_DIR"), "lab"); os.makedirs(LAB_DIR, exist_ok=True)
    with LOCK:
        S = _new_state()
        try:
            if os.path.exists(_p("lab_state.json")):
                old = json.load(open(_p("lab_state.json"), encoding="utf-8"))
                S.update(old)
                for k, d in SIGNALS: S["sigs"].setdefault(f"{k}:{d}", True)
        except Exception as e: _err("load state", e)
        RECENT[:] = _read_tail(_p("lab_trades.csv"), 300); SIGLOG[:] = _read_tail(_p("lab_signals.csv"), 150)
        PREV.clear(); PENDING.update(sid=None, t=0.0)
        READY = True
        pos = S.get("pos")
        if pos and pos.get("entry_day") != _now_s()[:10]:
            _close(pos, "CARRIED_OVER_RESTART", pos.get("last_bid") or pos["entry_px"], "STALE-LAST-BID", None, None, None, None, None, time.time(), datetime.now(IST), flags="app was restarted on another day")
        elif pos:
            _log(f"resumed open trade {pos['id']} after a restart"); _say(f"Auto Lab: resumed open trade {pos['id']}")
        _save()
    _log("Auto Lab ready")

def feed_keys():
    """Option instrument the lab needs live quotes for (the app subscribes to these)."""
    try:
        if READY and S and S.get("pos") and S["pos"].get("key"): return [S["pos"]["key"]]
    except Exception: pass
    return []


# ----------------------------------------------------------------------------- engine hook (cheap, runs about once a second)
def safe_hook(nw, wall, qs, items, verdict=None):
    if not READY: return
    try: _hook(nw, wall, qs, items, verdict)
    except Exception as e: _err("hook", e)

def _hook(nw, wall, qs, items, verdict):
    with LOCK:
        today = nw.strftime("%Y-%m-%d")
        if S["day"] != today: S["day"] = today; S["day_n"] = 0; S["day_pnl"] = 0.0
        states = {i["key"]: i["state"] for i in items}
        first = not PREV
        mo = bool(g("market_open")(nw))
        for key, d in SIGNALS:
            st, pst = states.get(key), PREV.get(key)
            if not first and mo and pst is not None and pst != st and st == d:
                _fired(nw, wall, qs, key, d, pst, items, verdict)
        PREV.clear(); PREV.update(states)
        _manage(nw, wall, qs, mo)

def _states_str(items):
    return ";".join(f"{i['key']}={i['state']}" for i in items)

def _fired(nw, wall, qs, key, d, pst, items, verdict):
    sid = f"{nw:%Y%m%d}-{key}-{d}-{int(wall * 1000)}"
    name = next((i["name"] for i in items if i["key"] == key), key)
    fq, sq = qs.get(g("FUT_KEY")) or {}, qs.get(g("UNDERLYING_KEY")) or {}
    S["fired"][f"{key}:{d}"] = S["fired"].get(f"{key}:{d}", 0) + 1
    why = None
    if not S["enabled"]: why = "SKIPPED_LAB_PAUSED"
    elif not S["sigs"].get(f"{key}:{d}", False): why = "SKIPPED_SIGNAL_OFF"
    elif pst == "NA" and wall - g("START_T", wall) < RULES["warmup_s"]: why = "SKIPPED_STARTUP_WARMUP"
    elif not (_hm(RULES["entry_from"]) <= nw.hour * 60 + nw.minute < _hm(RULES["entry_until"])): why = "SKIPPED_OUTSIDE_ENTRY_WINDOW"
    elif S["pos"] is not None or (PENDING["sid"] and wall - PENDING["t"] < 90): why = "SKIPPED_TRADE_ALREADY_OPEN"
    elif S["day_n"] >= RULES["safety_max_entries_per_day"]: why = "SKIPPED_SAFETY_CAP"
    elif not _fresh(fq, wall): why = "SKIPPED_NO_FRESH_FUTURES_QUOTE"
    ctx = dict(sid=sid, key=key, name=name, d=d, pst=pst, nw=nw, wall=wall, spot=sq.get("ltp"), fut=fq.get("ltp"), verdict=verdict, states=_states_str(items))
    if why:
        _sig_row(ctx, why, ""); return
    PENDING.update(sid=sid, t=wall)
    threading.Thread(target=_enter_worker, args=(ctx,), daemon=True, name="autolab-entry").start()

def _sig_row(c, action, trade_id):
    row = dict(signal_event_id=c["sid"], time_ist=_now_s(c["nw"]), ts=_r(c["wall"], 3), signal_key=c["key"], signal_name=c["name"], direction=c["d"], prev_state=c["pst"],
               action=action, trade_id=trade_id, spot=_r(c["spot"]), fut=_r(c["fut"]), verdict=c["verdict"], all_states=c["states"])
    try: _append(_p("lab_signals.csv"), SIG_COLS, row)
    except Exception as e: _err("signal log", e)
    SIGLOG.append({k: ("" if v is None else str(v)) for k, v in row.items()}); del SIGLOG[:-150]


# ----------------------------------------------------------------------------- entry (runs in its own thread: it calls the Upstox REST API)
def _chain(expiry):
    if requests is None: raise RuntimeError("requests not available")
    r = requests.get("https://api.upstox.com/v2/option/chain", headers=g("HDR"),
                     params={"instrument_key": g("UNDERLYING_KEY"), "expiry_date": str(expiry)}, timeout=15)
    r.raise_for_status()
    return r.json().get("data") or []

def _pick_expiry(today):
    exps = [e for e in g("exp_choices")() if e >= today]
    for e in exps:
        if (e - today).days >= RULES["min_dte"]: return e
    return exps[-1] if exps else None

def _opt_row(rows, d, strike_target):
    side = "call_options" if d == "BULL" else "put_options"
    best = None
    for x in rows:
        o = x.get(side) or {}
        if not o.get("instrument_key") or x.get("strike_price") is None: continue
        dist = abs(float(x["strike_price"]) - strike_target)
        if best is None or dist < best[0]: best = (dist, x, o)
    return best

def _enter_worker(c):
    sid, d = c["sid"], c["d"]
    try:
        today = c["nw"].date()
        expiry = _pick_expiry(today)
        if expiry is None: raise RuntimeError("no usable expiry")
        rows = _chain(expiry)
        spot = next((_f(x.get("underlying_spot_price")) for x in rows if _f(x.get("underlying_spot_price"))), None) or _f(c["spot"])
        if not spot: raise RuntimeError("no spot price")
        atm = round(spot / 50.0) * 50
        tgt = atm - RULES["itm_pts"] if d == "BULL" else atm + RULES["itm_pts"]
        pick = _opt_row(rows, d, tgt)
        if pick is None: raise RuntimeError("no option found in the chain")
        _dist, x, o = pick
        md, og = o.get("market_data") or {}, o.get("option_greeks") or {}
        bid, ask, ltp = _f(md.get("bid_price")), _f(md.get("ask_price")), _f(md.get("ltp"))
        slip = float(g("SLIP_FALLBACK", 0.5) or 0.5)
        flags = []
        if ask and ask > 0: px = ask
        elif ltp: px = ltp + slip; flags.append("NO_ASK_USED_LTP_PLUS_SLIP")
        else: raise RuntimeError("option has no price")
        if not (bid and bid > 0): flags.append("NO_BID")
        spread = _r((ask - bid) / ask * 100, 2) if (ask and bid and ask > 0) else None
        if spread is not None and spread > 15: flags.append("WIDE_SPREAD")
        with g("lock"): fq = dict((g("quotes") or {}).get(g("FUT_KEY")) or {})
        now_w = time.time()
        if not _fresh(fq, now_w): raise RuntimeError("no fresh futures quote at fill time")
        qty = int(g("LOT", 75))
        with LOCK:
            if S["pos"] is not None: raise RuntimeError("another trade is already open")
            if px * qty > S["start"] + S["realized"]: raise RuntimeError("not enough paper funds")
            S["seq"] += 1; S["day_n"] += 1
            tid = f"L{c['nw']:%Y%m%d}-{S['seq']:03d}"
            sign = 1 if d == "BULL" else -1
            nw2 = datetime.now(IST)
            pos = dict(id=tid, status="OPEN", sid=sid, entry_day=nw2.strftime("%Y-%m-%d"), key=o["instrument_key"], signal_key=c["key"], signal_name=c["name"], dir=d,
                       opt_type="CE" if d == "BULL" else "PE", expiry=str(expiry), dte=(expiry - today).days, strike=float(x["strike_price"]), qty=qty,
                       label=f"NIFTY {float(x['strike_price']):.0f} {'CE' if d == 'BULL' else 'PE'} {expiry:%d %b}",
                       fire_ts=c["wall"], fire_time=_now_s(c["nw"]), fut_at_fire=_r(c["fut"]), fill_ts=now_w, fill_time=_now_s(nw2),
                       entry_bid=_r(bid), entry_ask=_r(ask), entry_ltp=_r(ltp), entry_px=_r(px), entry_spread_pct=spread,
                       entry_delta=_r(og.get("delta"), 4), entry_iv=_r(og.get("iv"), 2), entry_oi=_r(md.get("oi"), 0), entry_volume=_r(md.get("volume"), 0),
                       entry_spot=_r(spot), entry_fut=_r(fq["ltp"], 2), target_fut=_r(fq["ltp"] + sign * RULES["target_pts"]), stop_fut=_r(fq["ltp"] - sign * RULES["stop_pts"]),
                       states=c["states"], verdict=c["verdict"], mfe=0.0, mae=0.0, t_mfe=0.0, t_mae=0.0, last_move=0.0, last_bid=_r(bid), opt_bid_max=_r(bid), opt_bid_min=_r(bid),
                       last_mark_ts=0.0, flags=";".join(flags), exiting=False)
            S["pos"] = pos; PENDING.update(sid=None, t=0.0); _save()
        _sig_row(c, "ENTERED", tid)
        _log(f"ENTER {tid} {pos['label']} @ {px} (ask {ask}, bid {bid}) fut {fq['ltp']} signal {c['key']} {d}")
        _say(f"Auto Lab bought {pos['label']} @ {px:.2f} on {c['key']} {d}")
    except Exception as e:
        with LOCK: PENDING.update(sid=None, t=0.0)
        _sig_row(c, f"ENTRY_FAILED: {str(e)[:80]}", "")
        _err("entry", e)


# ----------------------------------------------------------------------------- managing the open trade (each second)
def _manage(nw, wall, qs, mo):
    pos = S.get("pos")
    if not pos or pos.get("exiting"): return
    sign = 1 if pos["dir"] == "BULL" else -1
    fq, oq, sq = qs.get(g("FUT_KEY")) or {}, qs.get(pos["key"]) or {}, qs.get(g("UNDERLYING_KEY")) or {}
    ffresh, ofresh = _fresh(fq, wall), _fresh(oq, wall)
    age = wall - pos["fill_ts"]
    mv = None
    if ffresh:
        mv = (fq["ltp"] - pos["entry_fut"]) * sign; pos["last_move"] = _r(mv)
        if mv > pos["mfe"]: pos["mfe"], pos["t_mfe"] = mv, age
        if mv < pos["mae"]: pos["mae"], pos["t_mae"] = mv, age
    if ofresh and _f(oq.get("bid")) and oq["bid"] > 0:
        b = float(oq["bid"]); pos["last_bid"] = b
        pos["opt_bid_max"] = max(pos["opt_bid_max"] or b, b); pos["opt_bid_min"] = min(pos["opt_bid_min"] or b, b)
    if wall - pos["last_mark_ts"] >= RULES["mark_every_s"]:
        pos["last_mark_ts"] = wall
        try:
            est = None
            if pos.get("last_bid") is not None:
                est = (pos["last_bid"] - pos["entry_px"]) * pos["qty"] - g("charges")([("B", pos["entry_px"], pos["qty"]), ("S", pos["last_bid"], pos["qty"])])
            pos["unreal"] = _r(est)
            _append(_p("lab_marks.csv"), MARK_COLS, dict(trade_id=pos["id"], ts=_r(wall, 1), time_ist=_now_s(nw), fut=_r(fq.get("ltp")), spot=_r(sq.get("ltp")),
                    opt_bid=_r(oq.get("bid")), opt_ask=_r(oq.get("ask")), opt_ltp=_r(oq.get("ltp")), fut_move=_r(mv), unreal_net=_r(est)))
        except Exception as e: _err("mark", e)
    reason = None
    if mv is not None and mv >= RULES["target_pts"]: reason = "TARGET"
    elif mv is not None and mv <= -RULES["stop_pts"]: reason = "STOP"
    elif age >= RULES["max_hold_s"]: reason = "TIME_30M"
    elif (not mo) or nw.hour * 60 + nw.minute >= _hm(RULES["force_exit"]): reason = "END_OF_DAY"
    if reason: _begin_exit(pos, reason, nw, wall, qs)

def _begin_exit(pos, reason, nw, wall, qs):
    pos["exiting"] = True
    fq, oq, sq = qs.get(g("FUT_KEY")) or {}, qs.get(pos["key"]) or {}, qs.get(g("UNDERLYING_KEY")) or {}
    bid, ask, ltp = _f(oq.get("bid")), _f(oq.get("ask")), _f(oq.get("ltp"))
    slip = float(g("SLIP_FALLBACK", 0.5) or 0.5)
    if _fresh(oq, wall) and bid and bid > 0: _close(pos, reason, bid, "BID", bid, ask, ltp, fq.get("ltp"), sq.get("ltp"), wall, nw)
    elif _fresh(oq, wall) and ltp: _close(pos, reason, max(ltp - slip, 0.05), "LTP-SLIP", bid, ask, ltp, fq.get("ltp"), sq.get("ltp"), wall, nw)
    else: threading.Thread(target=_exit_worker, args=(pos, reason, nw, wall, fq.get("ltp"), sq.get("ltp")), daemon=True, name="autolab-exit").start()

def _exit_worker(pos, reason, nw, wall, fut, spot):
    bid = ask = ltp = None; src = "STALE-LAST-BID"; px = None
    try:
        rows = _chain(pos["expiry"]); side = "call_options" if pos["dir"] == "BULL" else "put_options"
        for x in rows:
            o = x.get(side) or {}
            if o.get("instrument_key") == pos["key"]:
                md = o.get("market_data") or {}; bid, ask, ltp = _f(md.get("bid_price")), _f(md.get("ask_price")), _f(md.get("ltp")); break
        slip = float(g("SLIP_FALLBACK", 0.5) or 0.5)
        if bid and bid > 0: px, src = bid, "REST-BID"
        elif ltp: px, src = max(ltp - slip, 0.05), "REST-LTP-SLIP"
    except Exception as e: _err("exit price", e)
    if px is None: px = pos.get("last_bid") or pos["entry_px"]
    with LOCK:
        if S.get("pos") is pos: _close(pos, reason, px, src, bid, ask, ltp, fut, spot, time.time(), datetime.now(IST))

def _close(pos, reason, px, src, bid, ask, ltp, fut, spot, wall, nw, flags=""):
    """Book the trade: always called with LOCK held (or during init)."""
    qty, ep = pos["qty"], pos["entry_px"]; px = float(px)
    gross = (px - ep) * qty
    try: chg = float(g("charges")([("B", ep, qty), ("S", px, qty)]))
    except Exception: chg = 0.0
    net = gross - chg
    S["realized"] += net; S["n"] += 1; S["day_pnl"] += net
    if net > 0: S["wins"] += 1; S["gross_win"] += net
    else: S["gross_loss"] += -net
    eq = S["start"] + S["realized"]; S["peak"] = max(S["peak"], eq); S["maxdd"] = max(S["maxdd"], S["peak"] - eq)
    sign = 1 if pos["dir"] == "BULL" else -1
    fmove = ((float(fut) - pos["entry_fut"]) * sign) if fut is not None else pos.get("last_move")
    row = dict(trade_id=pos["id"], day=pos["entry_day"], signal_key=pos["signal_key"], signal_name=pos["signal_name"], direction=pos["dir"], fire_time_ist=pos["fire_time"],
               fire_ts=_r(pos["fire_ts"], 3), fill_time_ist=pos["fill_time"], fill_latency_s=_r(pos["fill_ts"] - pos["fire_ts"], 2), expiry=pos["expiry"], dte=pos["dte"],
               strike=pos["strike"], opt_type=pos["opt_type"], instrument_key=pos["key"], qty=qty, lot_size=qty, entry_bid=pos["entry_bid"], entry_ask=pos["entry_ask"],
               entry_ltp=pos["entry_ltp"], entry_px=ep, entry_spread_pct=pos["entry_spread_pct"], entry_delta=pos["entry_delta"], entry_iv=pos["entry_iv"], entry_oi=pos["entry_oi"],
               entry_volume=pos["entry_volume"], entry_spot=pos["entry_spot"], entry_fut=pos["entry_fut"], fut_at_fire=pos["fut_at_fire"], target_fut=pos["target_fut"], stop_fut=pos["stop_fut"],
               all_states_at_fire=pos["states"], verdict_at_fire=pos["verdict"], exit_time_ist=_now_s(nw), exit_ts=_r(wall, 3), exit_reason=reason,
               hold_s=_r(wall - pos["fill_ts"], 1), exit_bid=_r(bid), exit_ask=_r(ask), exit_ltp=_r(ltp), exit_px=_r(px), exit_px_src=src, exit_spot=_r(spot), exit_fut=_r(fut),
               fut_move_signed=_r(fmove), fut_mfe=_r(pos["mfe"]), fut_mae=_r(pos["mae"]), t_mfe_s=_r(pos["t_mfe"], 1), t_mae_s=_r(pos["t_mae"], 1),
               opt_bid_max=pos["opt_bid_max"], opt_bid_min=pos["opt_bid_min"], gross_pnl=_r(gross), charges=_r(chg), net_pnl=_r(net),
               net_pct_of_premium=_r(net / (ep * qty) * 100, 2) if ep else None, equity_after=_r(eq), flags=";".join(x for x in (pos.get("flags"), flags) if x))
    try: _append(_p("lab_trades.csv"), TRADE_COLS, row)
    except Exception as e: _err("trade log", e)
    RECENT.append({k: ("" if v is None else str(v)) for k, v in row.items()}); del RECENT[:-300]
    S["pos"] = None; _save()
    _log(f"EXIT {pos['id']} {reason} px {px:.2f} ({src}) net {net:+.0f} equity {eq:,.0f}")
    _say(f"Auto Lab closed {pos['label']} ({reason}): net {'+' if net >= 0 else '-'}Rs{abs(net):,.0f}")


# ----------------------------------------------------------------------------- what the page shows
def snapshot():
    with LOCK:
        if not READY or S is None: return dict(ready=False)
        n, wins = S["n"], S["wins"]; losses = n - wins
        pos = S.get("pos"); today = S.get("day") or _now_s()[:10]
        unreal = (pos or {}).get("unreal") if pos else None
        eq = S["start"] + S["realized"] + (unreal or 0)
        p = None
        if pos:
            p = dict(id=pos["id"], label=pos["label"], dir=pos["dir"], signal=pos["signal_key"], qty=pos["qty"], entry_px=pos["entry_px"], entry_bid=pos["entry_bid"], bid=pos.get("last_bid"),
                     unreal=unreal, move=pos.get("last_move"), target=RULES["target_pts"], stop=RULES["stop_pts"], mfe=_r(pos["mfe"]), mae=_r(pos["mae"]),
                     age=_r(time.time() - pos["fill_ts"], 0), max_hold=RULES["max_hold_s"], entry_fut=pos["entry_fut"], target_fut=pos["target_fut"], stop_fut=pos["stop_fut"],
                     since=pos["fill_time"][11:], exiting=pos.get("exiting"), flags=pos.get("flags"))
        trades = [dict(id=r["trade_id"], day=r["day"], signal=r["signal_key"], dir=r["direction"], t_in=r["fill_time_ist"][11:16], t_out=r["exit_time_ist"][11:16],
                       label=f"{float(r['strike']):.0f} {r['opt_type']} {r['expiry'][5:]}", entry=r["entry_px"], exit=r["exit_px"], reason=r["exit_reason"], hold=r["hold_s"],
                       move=r["fut_move_signed"], net=r["net_pnl"]) for r in RECENT[-60:]]
        tday = [t for t in trades if t["day"] == today]
        sigs = [dict(key=f"{k}:{d}", name=k, dir=d, on=bool(S["sigs"].get(f"{k}:{d}")), total=S["fired"].get(f"{k}:{d}", 0)) for k, d in SIGNALS]
        log = [dict(t=r["time_ist"][11:19], key=r["signal_key"], dir=r["direction"], action=r["action"], trade=r["trade_id"], fut=r["fut"]) for r in SIGLOG[-40:]]
        return dict(ready=True, enabled=S["enabled"], sigs=sigs, rules=RULES, pos=p, trades=trades[::-1], today=tday[::-1], log=log[::-1], err=S.get("last_err"), pending=bool(PENDING["sid"]),
                    acct=dict(start=S["start"], equity=_r(eq), realized=_r(S["realized"]), today=_r(S["day_pnl"]), today_n=S["day_n"], n=n, wins=wins, losses=losses,
                              win_rate=_r(wins / n * 100, 1) if n else None, avg_win=_r(S["gross_win"] / wins) if wins else None, avg_loss=_r(S["gross_loss"] / losses) if losses else None,
                              pf=_r(S["gross_win"] / S["gross_loss"], 2) if S["gross_loss"] > 0 else None, expectancy=_r(S["realized"] / n) if n else None, maxdd=_r(S["maxdd"])))

def _command(j):
    c = j.get("cmd")
    qs_copy = None
    if c == "close":
        with g("lock"): qs_copy = {k: dict(v) for k, v in (g("quotes") or {}).items()}
    with LOCK:
        if not READY: return False, "not ready"
        if c == "enable": S["enabled"] = bool(j.get("on")); _save(); _log(f"trading {'ON' if S['enabled'] else 'PAUSED'}"); return True, ""
        if c == "signal":
            k = str(j.get("key"))
            if k not in S["sigs"]: return False, "unknown signal"
            S["sigs"][k] = bool(j.get("on")); _save(); return True, ""
        if c == "close":
            pos = S.get("pos")
            if not pos or pos.get("exiting"): return False, "no open trade"
            _begin_exit(pos, "MANUAL", datetime.now(IST), time.time(), qs_copy or {}); return True, ""
        if c == "reset":
            if S.get("pos") or PENDING["sid"]: return False, "close the open trade first"
            arc = _p("archive_" + datetime.now(IST).strftime("%Y%m%d_%H%M%S")); os.makedirs(arc, exist_ok=True)
            for f in ("lab_trades.csv", "lab_signals.csv", "lab_marks.csv", "lab_state.json", "lab_events.log"):
                if os.path.exists(_p(f)): shutil.move(_p(f), os.path.join(arc, f))
            keep = dict(enabled=S["enabled"], sigs=dict(S["sigs"])); S.clear(); S.update(_new_state()); S.update(keep); RECENT.clear(); SIGLOG.clear(); _save()
            _log("account reset; old data archived to " + os.path.basename(arc)); return True, ""
    return False, "unknown command"

def register_routes(app):
    from flask import jsonify, request, Response
    @app.route("/api/lab/state", endpoint="lab_state")
    def _state(): return jsonify(snapshot())
    @app.route("/api/lab/cmd", methods=["POST"], endpoint="lab_cmd")
    def _cmd():
        ok, msg = _command(request.json or {})
        return (jsonify(ok=True) if ok else (jsonify(ok=False, error=msg), 400))
    @app.route("/api/lab/<name>.csv", endpoint="lab_csv")
    def _csv(name):
        f = {"trades": "lab_trades.csv", "signals": "lab_signals.csv", "marks": "lab_marks.csv"}.get(name)
        if not f: return Response("not found", 404)
        data = open(_p(f), "rb").read() if READY and os.path.exists(_p(f)) else b""
        return Response(data, mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={f}"})


# ----------------------------------------------------------------------------- the page (added to the dashboard when it starts)
LAB_CSS = "body[data-view=lab] .main{grid-template-columns:1fr}body[data-view=lab] #vPanel,body[data-view=lab] #vChart{display:none}body[data-view=lab] #vLab{display:block}"
LAB_PAGE = '<div class="page" id="vLab"><h3 style="margin:0 0 4px">🤖 Auto Lab</h3><div id="labBox"></div></div>\n'
LAB_JS = r"""
/* ---------- Auto Lab ---------- */
async function labLoad(){try{const d=await (await fetch('/api/lab/state')).json();labRender(d);}catch(e){$('labBox').innerHTML='<p style="color:#ef5350">Could not load Auto Lab: '+e+'</p>';}}
function labCmd(o){return post('/api/lab/cmd',o).then(()=>labLoad());}
setInterval(()=>{if(view==='lab')labLoad();},1500);
function labRender(d){
 if(!d.ready){$('labBox').innerHTML='<p style="color:var(--mut)">Auto Lab is starting…</p>';return;}
 const a=d.acct,p=d.pos,R=d.rules;
 const f=(x,n=2)=>x==null?'–':Number(x).toFixed(n);
 const m=x=>x==null?'–':money(x);
 const dur=s=>{s=Math.max(0,Math.round(s));return Math.floor(s/60)+'m '+String(s%60).padStart(2,'0')+'s';};
 let h='<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:6px 0 12px"><button class="'+(d.enabled?'green':'red')+'" onclick="labCmd({cmd:\'enable\',on:'+(!d.enabled)+'})">'+(d.enabled?'● AUTO-TRADING ON — click to pause':'○ PAUSED — click to start')+'</button><span style="color:var(--mut);max-width:760px">Paper money only. Its own account, trades and data (folder spread_data/lab), separate from everything else on this dashboard. It buys 1 lot by itself when a chosen signal turns on.</span></div>';
 h+='<div class="cards" style="grid-template-columns:repeat(4,1fr)">'
  +card('Lab equity','₹'+Math.round(a.equity).toLocaleString('en-IN'),'started with ₹'+Math.round(a.start).toLocaleString('en-IN'))
  +card('Today','<span class="'+cls(a.today)+'">'+m(a.today)+'</span>',a.today_n+' trade(s) today')
  +card('All-time P&L','<span class="'+cls(a.realized)+'">'+m(a.realized)+'</span>',a.n+' closed trades')
  +card('Win rate',a.win_rate==null?'–':a.win_rate+'%',a.wins+' won / '+a.losses+' lost')
  +card('Average win',m(a.avg_win),'average loss '+m(a.avg_loss==null?null:-a.avg_loss))
  +card('Profit factor',a.pf==null?'–':a.pf,'total wins ÷ total losses (above 1 = profitable)')
  +card('Per trade (expectancy)',m(a.expectancy),'average result of one trade after all costs')
  +card('Worst drop (max drawdown)',a.maxdd==null?'–':'−₹'+Math.round(a.maxdd).toLocaleString('en-IN'),'from the account\'s highest point')+'</div>';
 h+='<h4>Signals it trades</h4><div style="display:flex;gap:18px;flex-wrap:wrap">'+d.sigs.map(s=>'<label style="display:flex;gap:6px;align-items:center"><input type="checkbox" '+(s.on?'checked':'')+' onchange="labCmd({cmd:\'signal\',key:\''+s.key+'\',on:this.checked})"> <b>'+s.name+' '+s.dir+'</b> <span style="color:var(--mut)">'+(s.dir==='BULL'?'→ buys a call':'→ buys a put')+' · fired '+s.total+'×</span></label>').join('')+'</div>';
 h+='<h4>The rules (fixed so the test stays honest)</h4><div style="color:var(--mut);max-width:900px;line-height:1.5">Buys 1 lot of an option about '+R.itm_pts+' points in the money at the ask price, on the nearest expiry that is at least '+R.min_dte+' days away. Sells at the bid when Nifty futures have moved <b class="up">+'+R.target_pts+'</b> points in its favour (target) or <b class="dn">−'+R.stop_pts+'</b> points against it (stop), after '+(R.max_hold_s/60)+' minutes, or at '+R.force_exit+'. New trades only between '+R.entry_from+' and '+R.entry_until+'. One trade at a time, no daily limit. Brokerage, STT and other charges are deducted.</div>';
 h+='<h4>Open trade</h4>';
 if(p){const pct=Math.max(-1,Math.min(1,(p.move||0)/(p.move>=0?p.target:p.stop)));
  h+='<div class="card" style="max-width:760px"><div class="l">'+p.label+' · bought on '+p.signal+' '+p.dir+' at '+p.since+(p.exiting?' · <b>closing…</b>':'')+'</div><div class="v"><span class="'+cls(p.unreal||0)+'">'+m(p.unreal)+'</span> <span style="font-size:13px;color:var(--mut)">now (after exit costs)</span></div>'
   +'<div class="s">Paid '+f(p.entry_px)+' per unit (ask when bought) · bid now '+f(p.bid)+' · '+p.qty+' units</div>'
   +'<div class="s" style="margin-top:4px">Nifty futures moved <b class="'+cls(p.move||0)+'">'+f(p.move,1)+'</b> pts from '+f(p.entry_fut,1)+' · target '+f(p.target_fut,1)+' · stop '+f(p.stop_fut,1)+' · best '+f(p.mfe,1)+' / worst '+f(p.mae,1)+'</div>'
   +'<div style="height:8px;background:#1a2330;border-radius:5px;margin:8px 0;position:relative"><div style="position:absolute;left:50%;width:2px;height:8px;background:#667"></div><div style="position:absolute;top:0;height:8px;border-radius:5px;background:'+(pct>=0?'#26a69a':'#ef5350')+';left:'+(pct>=0?50:50+pct*50)+'%;width:'+Math.abs(pct)*50+'%"></div></div>'
   +'<div class="s">Open for '+dur(p.age)+' of '+dur(p.max_hold)+' maximum'+(p.flags?' · notes: '+p.flags:'')+'</div><button class="red" style="margin-top:8px" onclick="if(confirm(\'Close this Auto Lab trade now?\'))labCmd({cmd:\'close\'})">Close now</button></div>';
 }else h+='<p style="color:var(--mut)">'+(d.pending?'A signal just fired: buying…':'No open trade. Waiting for the next signal.')+'</p>';
 const tt=rows=>rows.length?'<table><tr><th>In</th><th>Out</th><th>Signal</th><th>Option</th><th>Paid</th><th>Sold</th><th>Futures moved</th><th>Why it closed</th><th>Held</th><th>Net P&L</th></tr>'+rows.map(t=>'<tr><td>'+t.t_in+'</td><td>'+t.t_out+'</td><td>'+t.signal+' '+t.dir+'</td><td>'+t.label+'</td><td>'+f(t.entry)+'</td><td>'+f(t.exit)+'</td><td class="'+cls(+t.move)+'">'+f(t.move,1)+'</td><td>'+t.reason+'</td><td>'+dur(+t.hold)+'</td><td class="'+cls(+t.net)+'"><b>'+m(+t.net)+'</b></td></tr>').join('')+'</table>':'<p style="color:var(--mut)">None yet.</p>';
 h+='<h4>Today\'s trades</h4>'+tt(d.today)+'<h4>Recent trades (latest first)</h4>'+tt(d.trades);
 h+='<h4>Every time one of these signals fired (and what Auto Lab did)</h4>'+(d.log.length?'<table><tr><th>Time</th><th>Signal</th><th>What happened</th><th>Trade</th><th>Futures</th></tr>'+d.log.map(r=>'<tr><td>'+r.t+'</td><td>'+r.key+' '+r.dir+'</td><td style="text-align:left">'+r.action+'</td><td>'+(r.trade||'')+'</td><td>'+(r.fut||'')+'</td></tr>').join('')+'</table>':'<p style="color:var(--mut)">No signals yet today.</p>');
 h+='<p style="margin-top:14px"><a class="btn" href="/api/lab/trades.csv" download>Download trades.csv</a> <a class="btn" href="/api/lab/signals.csv" download>Download signals.csv</a> <a class="btn" href="/api/lab/marks.csv" download>Download marks.csv</a> <span style="color:var(--mut)">trades = one row per trade with full detail · signals = every firing and what was done · marks = price path every 5 s while a trade was open</span></p>';
 h+='<p><button class="red" onclick="if(confirm(\'Archive ALL Auto Lab data and start a fresh account?\'))labCmd({cmd:\'reset\'})">Reset Auto Lab (old data is archived, not deleted)</button></p>';
 if(d.err)h+='<p style="color:#ef5350">Last error: '+d.err+'</p>';
 $('labBox').innerHTML=h;}
"""

def inject(html):
    """Add the Auto Lab tab to the dashboard page. If any anchor is missing the page is left as it was."""
    try:
        out = html
        def rep(old, new):
            nonlocal out
            if out.count(old) != 1: raise ValueError("anchor not found exactly once: " + old[:48])
            out = out.replace(old, new)
        rep("</style></head><body>", LAB_CSS + "</style></head><body>")
        rep('<div class="page" id="vSettings">', LAB_PAGE + '<div class="page" id="vSettings">')
        rep("['score','🏆 Scorecard'],", "['score','🏆 Scorecard'],['lab','🤖 Auto Lab'],")
        rep("if(v==='score')loadScore();", "if(v==='score')loadScore();if(v==='lab')labLoad();")
        rep("['position','signals','score','trades','settings'].includes(prev)", "['position','signals','score','lab','trades','settings'].includes(prev)")
        rep("</script></body></html>", LAB_JS + "</script></body></html>")
        return out
    except Exception as e:
        print("[autolab] the Auto Lab tab was NOT added:", e, flush=True)
        return html
