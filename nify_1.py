#!/usr/bin/env python3
"""
NIFTY BULL PUT SPREAD - LOCAL LIVE PAPER TRADING DASHBOARD
Run:   python nifty_spread_dashboard.py
It asks for your Upstox read-only Analytics Token (hidden), then opens the dashboard in your browser.
No real orders are ever placed. Everything is saved in ./spread_data next to this file.
"""
import sys, subprocess, importlib.util

def _ensure():
    need = {"flask": "flask", "upstox_client": "upstox-python-sdk", "pandas": "pandas",
            "requests": "requests", "numpy": "numpy"}
    miss = [p for m, p in need.items() if importlib.util.find_spec(m) is None]
    if miss:
        print("Installing:", ", ".join(miss)); subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *miss])
_ensure()
try:   # auto-close any older copy of this script still holding the Upstox websocket
    if importlib.util.find_spec("psutil") is None: subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "psutil"])
    import psutil, os as _os
    for _p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (_p.info["pid"] != _os.getpid() and "python" in (_p.info["name"] or "").lower()
                    and "nifty_spread_dashboard" in " ".join(_p.info["cmdline"] or [])):
                print(f"Closing old session PID {_p.info['pid']}"); _p.terminate(); _p.wait(5)
        except Exception: pass
except Exception as _e: print("old-session cleanup skipped:", _e)

import json, math, os, time, threading, getpass, queue, shutil, webbrowser, logging
from urllib.parse import quote as urlq
import requests, numpy as np, pandas as pd
from datetime import datetime, timedelta, date, time as dtime, timezone
from flask import Flask, jsonify, request, Response
import upstox_client
import persist; persist.restore_early()   # cloud only: restore saved data from the private data repo before anything reads it
import autolab

IST = timezone(timedelta(hours=5, minutes=30))
SHIFT = 19800                                   # chart shows IST by shifting epoch seconds

# =============================== SETTINGS (also editable live in the dashboard) ===============================
UNDERLYING_KEY  = "NSE_INDEX|Nifty 50"
UNDERLYING_NAME = "NIFTY"
EXPIRY_TYPE     = "weekly"    # "monthly" or "weekly"
EXPIRY_OVERRIDE = ""          # e.g. "2026-11-24"; empty = auto
MIN_DTE_ENTRY   = 3
SHORT_MODE      = "delta"     # "delta" or "atm"
SHORT_DELTA     = 0.30
HEDGE_MODE      = "delta"     # "delta" or "points"
HEDGE_DELTA     = 0.15
HEDGE_POINTS    = 200
LOTS            = 1
ENTRY_TIME      = "now"       # "now" or "HH:MM" IST
ENTRY_CUTOFF    = "15:00"
TARGET_PCT         = 30
BUFFER_LOSS_X      = 1.0
BUFFER_SHORT_DELTA = 0.45
MAX_HOLD_DAYS      = 3
EXIT_TIME          = "15:29"
MIN_DTE_EXIT       = 2
SLIP_FALLBACK   = 1.0
START           = "auto"      # "auto" resume/stay flat/new  |  "fresh" ignore saved trade
AUTO_REENTER            = True
REENTER_AFTER_RULE_EXIT = False
REENTER_COOLDOWN_SEC    = 30
MAX_TRADES_PER_DAY      = 5
USE_REST_FALLBACK = False   # websocket only
# ---- Bear call spread (CALL side) settings: used when a bearish signal / "Enter bear call" is chosen
CALL_LOTS = 1; CALL_SHORT_MODE = "delta"; CALL_SHORT_DELTA = 0.30; CALL_HEDGE_MODE = "delta"; CALL_HEDGE_DELTA = 0.15; CALL_HEDGE_POINTS = 200
CALL_TARGET_PCT = 30; CALL_BUFFER_LOSS_X = 1.0; CALL_BUFFER_SHORT_DELTA = 0.45
INTRADAY_ONLY = True          # strictly intraday: every trade is squared off at EXIT_TIME the same day
# ---- Signals
SIG_USE_VWAP = True; SIG_USE_RVOL = True; SIG_USE_ORB = True; SIG_USE_STRUCT = True; SIG_USE_EMA = True
SIG_USE_DELTA = False; SIG_USE_SWEEP = False; SIG_USE_VWBAND = False      # new signals 8-10 (off until you tick them)
SIG_USE_ORBV = False; SIG_USE_DTREND = False                                # new signals 11-12 (off until you tick them)
SIG_VWAP_COMPRESS = 0.8      # signal 1: the gap between price and VWAP may shrink to this fraction of what it was before the bias is called "compressing"
SIG_DT_BARS = 4; SIG_DT_MIN_RATIO = 0.02    # signal 12: 5-min candles looked at (even number), and net delta needed as a fraction of volume (0.02 = 2%)
SIG_DELTA_LOOKBACK = 30; SIG_DELTA_MIN_BARS = 12; SIG_DELTA_FRESH = 8      # delta divergence: minutes looked at / needed / how recent the 2nd swing must be
SIG_SWEEP_LOOKBACK = 20; SIG_SWEEP_CLOSE_FRAC = 0.6; SIG_SWEEP_MIN_PTS = 4.0; SIG_SWEEP_MIN_DEPTH = 2.0   # sweep: lookback minutes, close in top/bottom 40% of candle, min candle size, wick must poke this many pts past the old low/high
SIG_VWB_MIN_BARS = 20; SIG_VWB_MIN_SIGMA = 3.0; SIG_VWB_EXT = 2.0; SIG_VWB_SLOPE_MIN = 5; SIG_VWB_FLAT = 1.0; SIG_VWB_WINDOW = 8   # VWAP bands: minutes needed, slope window, "flat" = within this many pts, look-back for the dip/spike
SIG_COMBINE = "all"           # all = every ticked signal must agree | any = one signal, none opposing | majority
SIG_AUTO_TRADE = False        # bullish verdict -> bull put spread, bearish verdict -> bear call spread
SIG_EXIT_ON_OPPOSITE = True; SIG_NOTIFY = True
MAX_OPEN_TRADES = 99          # how many trades may be open at the same time
SIG_MAX_PER_SIDE = 1          # most signal-driven trades open per side (bull put / bear call)
SIG_USE_OI = False; SIG_USE_CONTRIB = False; SIG_OI_BUFFER = 10; SIG_CONTRIB_MIN = 20.0
# Approximate Nifty 50 weights (%) of the biggest stocks. They drift over time: edit them whenever you like.
NIFTY_WEIGHTS = {"HDFCBANK": 12.8, "ICICIBANK": 9.2, "RELIANCE": 8.3, "BHARTIARTL": 5.2, "INFY": 5.0, "LT": 3.9, "ITC": 3.4,
                 "TCS": 3.1, "AXISBANK": 3.0, "SBIN": 2.9, "M&M": 2.6, "KOTAKBANK": 2.6, "HINDUNILVR": 2.1, "BAJFINANCE": 2.0}
SR_TOL_PCT = 0.08              # pivots closer than this (% of price) count as one level
FUT_LOTS = 1; FUT_TARGET_PTS = 40.0; FUT_STOP_PTS = 25.0       # futures trades have their own lots / target / stop
PARTIAL_ON = False; PARTIAL_AT_PCT = 50.0; PARTIAL_CLOSE_PCT = 50.0; PARTIAL_MOVE_STOP = True
SC_MIN_MOVE = 8.0           # scorecard: a move smaller than this (points) after a signal is ignored, it is neither right nor wrong
SHOW_SR_LINES = True; SHOW_DMA_LINES = False; RECORD_TICKS = True; BACKTEST_MODE = False
TRADE_MODE = "options"        # "options" (spreads + single options) or "futures" (trade the NIFTY futures contract itself)
NK_LOTS = 1; NK_OPT = "CE"; NK_STRIKE_MODE = "atm"; NK_DELTA = 0.30; NK_TARGET_PTS = 40.0; NK_STOP_PTS = 25.0; FUT_MARGIN_PCT = 12.0
SIG_VWAP_BUFFER = 3.0     # signal 1: price must be this many points beyond VWAP (above for bullish, below for bearish) before it says bullish or bearish
SIG_USE_DMA = False; DMA_FAST = 20; DMA_SLOW = 50; DMA_LONG = 200
SIG_USE_VWX = False                # signal 14 (VWAP first cross): off in the overall view until you tick it, so trading is unchanged
SIG_VWX_QUIET = 10; SIG_VWX_HOLD = 15     # quiet minutes with no same-side turn of signal 1 before; minutes the bias is held
SIG_RVOL_MIN = 1.5; SIG_SLOPE_MIN = 10; SIG_COOLDOWN_MIN = 10
START_EQUITY    = 500000      # default paper-account starting equity in Rs (you are asked on first run; changeable via "Clear all" in the dashboard)
PORT            = int(os.environ.get("PORT", 8050))   # Render tells us which port to use
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spread_data")
# ===============================================================================================================

EDITABLE = {"SC_MIN_MOVE": float,
            "SHOW_SR_LINES": bool, "SHOW_DMA_LINES": bool, "RECORD_TICKS": bool, "BACKTEST_MODE": bool,
            "FUT_LOTS": int, "FUT_TARGET_PTS": float, "FUT_STOP_PTS": float, "PARTIAL_ON": bool, "PARTIAL_AT_PCT": float, "PARTIAL_CLOSE_PCT": float, "PARTIAL_MOVE_STOP": bool,
            "TRADE_MODE": ("options", "futures"), "NK_LOTS": int, "NK_OPT": ("CE", "PE"), "NK_STRIKE_MODE": ("atm", "delta"), "NK_DELTA": float,
            "NK_TARGET_PTS": float, "NK_STOP_PTS": float, "FUT_MARGIN_PCT": float, "SIG_VWAP_BUFFER": float,
            "SIG_USE_DMA": bool, "SIG_USE_VWX": bool, "SIG_VWX_QUIET": int, "SIG_VWX_HOLD": int, "DMA_FAST": int, "DMA_SLOW": int, "DMA_LONG": int,
            "MAX_OPEN_TRADES": int, "SIG_MAX_PER_SIDE": int, "SIG_USE_OI": bool, "SIG_USE_CONTRIB": bool, "SIG_OI_BUFFER": int, "SIG_CONTRIB_MIN": float,
            "INTRADAY_ONLY": bool, "SIG_AUTO_TRADE": bool, "SIG_COMBINE": ("all", "any", "majority"), "SIG_EXIT_ON_OPPOSITE": bool, "SIG_NOTIFY": bool,
            "SIG_USE_VWAP": bool, "SIG_USE_RVOL": bool, "SIG_USE_ORB": bool, "SIG_USE_STRUCT": bool, "SIG_USE_EMA": bool, "SIG_USE_DELTA": bool, "SIG_USE_ORBV": bool, "SIG_USE_DTREND": bool, "SIG_USE_SWEEP": bool, "SIG_USE_VWBAND": bool,
            "SIG_RVOL_MIN": float, "SR_TOL_PCT": float, "SIG_SLOPE_MIN": int, "SIG_COOLDOWN_MIN": int,
            "CALL_LOTS": int, "CALL_SHORT_MODE": ("delta", "atm"), "CALL_SHORT_DELTA": float, "CALL_HEDGE_MODE": ("delta", "points"),
            "CALL_HEDGE_DELTA": float, "CALL_HEDGE_POINTS": int, "CALL_TARGET_PCT": float, "CALL_BUFFER_LOSS_X": float, "CALL_BUFFER_SHORT_DELTA": float,
            "TARGET_PCT": float, "BUFFER_LOSS_X": float, "BUFFER_SHORT_DELTA": float, "MAX_HOLD_DAYS": int,
            "EXIT_TIME": "time", "MIN_DTE_EXIT": int, "LOTS": int, "ENTRY_TIME": "time_or_now", "ENTRY_CUTOFF": "time",
            "SHORT_MODE": ("delta", "atm"), "SHORT_DELTA": float, "HEDGE_MODE": ("delta", "points"),
            "HEDGE_DELTA": float, "HEDGE_POINTS": int, "REENTER_AFTER_RULE_EXIT": bool,
            "REENTER_COOLDOWN_SEC": int, "MAX_TRADES_PER_DAY": int, "SLIP_FALLBACK": float}

SIG_RF_VWAP = 0                      # seconds between checks. 0 = checked on EVERY futures tick (signal 1 only); the rest are checked every few seconds
for _k in ("RVOL", "ORB", "STRUCT", "EMA", "OI", "CONTRIB", "DELTA", "SWEEP", "VWBAND", "ORBV", "DTREND", "DMA", "VWX"): globals()["SIG_RF_"+_k] = 5
SIG_RF_VWX = 1                       # signal 14 reads signal 1 once a second
EDITABLE.update({"SIG_RF_"+_k: int for _k in ("VWAP", "RVOL", "ORB", "STRUCT", "EMA", "OI", "CONTRIB", "DELTA", "SWEEP", "VWBAND", "ORBV", "DTREND", "DMA", "VWX")})
EDITABLE.update({"SIG_VWAP_COMPRESS": float, "SIG_DT_BARS": int, "SIG_DT_MIN_RATIO": float, "SIG_DELTA_LOOKBACK": int, "SIG_DELTA_MIN_BARS": int, "SIG_DELTA_FRESH": int,
                 "SIG_SWEEP_LOOKBACK": int, "SIG_SWEEP_CLOSE_FRAC": float, "SIG_SWEEP_MIN_PTS": float, "SIG_SWEEP_MIN_DEPTH": float,
                 "SIG_VWB_MIN_BARS": int, "SIG_VWB_MIN_SIGMA": float, "SIG_VWB_EXT": float, "SIG_VWB_SLOPE_MIN": float, "SIG_VWB_FLAT": float, "SIG_VWB_WINDOW": int})
os.makedirs(DATA_DIR, exist_ok=True)
STATE_PATH, LEDGER = f"{DATA_DIR}/state.json", f"{DATA_DIR}/trades.csv"
AUDIT, SESSION_PATH = f"{DATA_DIR}/events.log", f"{DATA_DIR}/session.json"
ACCOUNT_PATH = f"{DATA_DIR}/account.json"
SETTINGS_PATH = f"{DATA_DIR}/settings.json"
ACCOUNT = dict(start=float(START_EQUITY), realized=0.0, peak=float(START_EQUITY), maxdd=0.0, created="", resets=0)

R_, Q_ = 0.065, 0.013
lock = threading.Lock(); clock = threading.Lock()
import csv, collections
from collections.abc import MutableMapping
import contextlib
POSITIONS = []; SEL = {"pid": None}; PID = [0]; GLOG = []; _EMPTY = {}; _tls = threading.local()
class _State(MutableMapping):
    """Behaves like the old single `state` dict, but points at whichever trade is being worked on
    (engine thread: the trade in `use(pos)`; web threads: the trade selected on the dashboard)."""
    def _t(self):
        t = getattr(_tls, "cur", None)
        if t is not None: return t
        for p in POSITIONS:
            if p.get("pid") == SEL["pid"]: return p
        return POSITIONS[-1] if POSITIONS else _EMPTY
    def __getitem__(self, k): return self._t()[k]
    def __setitem__(self, k, v): self._t()[k] = v
    def __delitem__(self, k): del self._t()[k]
    def __iter__(self): return iter(self._t())
    def __len__(self): return len(self._t())
    def clear(self): self._t().clear()
    def update(self, *a, **k): self._t().update(*a, **k)
@contextlib.contextmanager
def use(pos):
    prev = getattr(_tls, "cur", None); _tls.cur = pos
    try: yield pos
    finally: _tls.cur = prev
quotes = {}; RAW = []; state = _State(); session = {}; ACTIVE_PAIRS = []; SUBSCRIBED = set(); SEED_PEND = set(); EQ_KEYS = {}; OI_CACHE = {}; OI_LAST = 0.0
CANDLES = {}; SEEDED = set(); CUR_LEGS = ("", "")
MSGS = []; SNAP = {"status": "STARTING"}
cmdq = queue.Queue(); ENGINE_STOP = threading.Event()
AUTO = AUTO_REENTER; FORCE = False; REPLAN_NOW = False
TOKEN = ""; HDR = {}; opts = None; LOT = 75; EXPIRY0 = None
streamer = None; feed_open = False

def now_ist(): return datetime.now(IST)
def hm(s): h, m = map(int, s.split(":")); return h*60+m
def mins(n): return n.hour*60+n.minute
def market_open(n): return n.weekday() < 5 and hm("09:15") <= mins(n) < hm("15:30")
def money(x): return f"{'+' if x >= 0 else '-'}Rs{abs(x):,.0f}"

def charges(orders):
    b = txn = sebi = ipft = stt = stamp = 0.0
    for side, px, q in orders:
        t = px*q; b += 20; txn += t*0.0003503; sebi += t*1e-6; ipft += t*5e-6
        if side == "S": stt += t*0.0015
        else: stamp += t*0.00003
    return b + txn + sebi + ipft + 0.18*(b+txn+sebi+ipft) + stt + stamp

def _ncdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def _npdf(x): return math.exp(-0.5*x*x)/math.sqrt(2*math.pi)
FUT_KEY = ""
def n_live(side=None):
    return sum(1 for p_ in POSITIONS if p_["status"] in ("OPEN", "WAITING") and not p_.get("idle") and (side is None or p_.get("side") == side))
PLAN0 = None
def make_idle():
    if PLAN0 is None: return
    PID[0] += 1; ns = new_state(PLAN0); ns.update(pid=PID[0], origin="idle", idle=True); POSITIONS.append(ns); SEL["pid"] = ns["pid"]; set_cur_legs()
def pos_legs(p):
    src = p if p.get("status") != "WAITING" else p.get("plan", {}); return (src["short"]["key"], src["long"]["key"])
def feed_keys():
    ks = [UNDERLYING_KEY, FUT_KEY, *EQ_KEYS.values(), *CUR_LEGS, *autolab.feed_keys()]
    for p in list(POSITIONS):
        if p.get("status") != "CLOSED" or p.get("pid") == SEL["pid"]: ks += list(pos_legs(p))
    return list(dict.fromkeys(k for k in ks if k))
def refresh_pairs():
    global ACTIVE_PAIRS
    label_keys()
    pairs = [pos_legs(p) for p in list(POSITIONS) if p.get("status") != "CLOSED"]+([CUR_LEGS] if CUR_LEGS[0] else [])
    ACTIVE_PAIRS = list(dict.fromkeys(pairs)); new = [k for pr in ACTIVE_PAIRS for k in pr if k not in SEEDED and k not in SEED_PEND]
    if new: SEED_PEND.update(new); seed_async(*new)
def sync_subs():
    want = set(feed_keys()); add, rem = list(want-SUBSCRIBED), list(SUBSCRIBED-want)
    if (add or rem) and streamer is not None and feed_open:
        change_subs(rem, add); SUBSCRIBED.clear(); SUBSCRIBED.update(want)
def cur_side():
    return (state.get("side") or (state.get("plan") or {}).get("side") or "PUT")
def OT(): return "CE" if cur_side() == "CALL" else "PE"
def P(n):   # per-side setting (CALL_ variant when the current spread is a bear call)
    return globals()[("CALL_" if cur_side() == "CALL" else "")+n]
def bs_call(S, K, T, iv):
    F = S*math.exp((R_-Q_)*T); sd = iv*math.sqrt(T)
    d1 = (math.log(F/K)+0.5*sd*sd)/sd; d2 = d1-sd
    return math.exp(-R_*T)*(F*_ncdf(d1)-K*_ncdf(d2)), math.exp(-Q_*T)*_ncdf(d1), S*math.exp(-Q_*T)*_npdf(d1)*math.sqrt(T)/100
def bs_put(S, K, T, iv):
    F = S*math.exp((R_-Q_)*T); sd = iv*math.sqrt(T)
    d1 = (math.log(F/K)+0.5*sd*sd)/sd; d2 = d1-sd
    price = math.exp(-R_*T)*(K*_ncdf(-d2)-F*_ncdf(-d1))
    return price, -math.exp(-Q_*T)*_ncdf(-d1), S*math.exp(-Q_*T)*_npdf(d1)*math.sqrt(T)/100
def calc_greeks(S, K, expiry, price):
    bs = bs_call if cur_side() == "CALL" else bs_put
    T = max((datetime.combine(expiry, dtime(15, 30), IST)-now_ist()).total_seconds()/(365*86400), 1e-5)
    intr = (S*math.exp(-Q_*T)-K*math.exp(-R_*T)) if cur_side() == "CALL" else (K*math.exp(-R_*T)-S*math.exp(-Q_*T))
    if price <= max(intr, 0)+0.05: return None
    lo, hi = 0.01, 2.0
    for _ in range(60):
        mid = (lo+hi)/2
        if bs(S, K, T, mid)[0] > price: hi = mid
        else: lo = mid
    iv = (lo+hi)/2; p, d, v = bs(S, K, T, iv)
    return dict(delta=d, iv=iv*100, vega=v, theta=bs(S, K, max(T-1/365, 1e-5), iv)[0]-p)

def exp_date(): return date.fromisoformat(state["expiry"])
def spot_of(qs): return (qs.get(UNDERLYING_KEY) or {}).get("ltp")
def feed_age(qs, wall):
    t = (qs.get(UNDERLYING_KEY) or {}).get("t"); return None if t is None else wall-t
def px_entry_short(q): b = q.get("bid"); return b if b and b > 0 else q["ltp"]-SLIP_FALLBACK
def px_entry_long(q):  a = q.get("ask"); return a if a and a > 0 else q["ltp"]+SLIP_FALLBACK
def px_exit_short(q):  a = q.get("ask"); return a if a and a > 0 else q["ltp"]+SLIP_FALLBACK
def px_exit_long(q):   b = q.get("bid"); return b if b and b > 0 else max(q["ltp"]-SLIP_FALLBACK, 0.05)

def leg_greeks(q, strike, spot):
    if not strike or strike <= 0: return dict(delta=None, theta=None, vega=None, iv=None, src="-")
    if q.get("delta") is not None:
        return dict(delta=q["delta"], theta=q.get("theta"), vega=q.get("vega"), iv=q.get("iv"), src="feed")
    if spot and q.get("ltp"):
        g = calc_greeks(spot, strike, exp_date(), q["ltp"])
        if g: g["src"] = "calc"; return g
    return dict(delta=None, theta=None, vega=None, iv=None, src="-")

# ------------------------------------------------------------------ logging / saving
def log(msg):
    line = f"{now_ist():%d-%b %H:%M:%S}  {msg}"
    GLOG.append(line); del GLOG[:-200]; state.setdefault("log", []).append(line); state["log"] = state["log"][-60:]
    with open(AUDIT, "a", encoding="utf-8") as f: f.write(line + "\n")
    print(line)
def say(t):
    MSGS.append(dict(id=(MSGS[-1]["id"]+1 if MSGS else 1), t=now_ist().strftime("%H:%M:%S"), text=t)); del MSGS[:-10]; print("  >", t)
def _atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(obj, f, default=str)
    os.replace(tmp, path)
def save_state(): _atomic(STATE_PATH, dict(positions=POSITIONS, sel=SEL["pid"], pidc=PID[0]))
def save_session(): _atomic(SESSION_PATH, session)
def save_account(): _atomic(ACCOUNT_PATH, ACCOUNT)

def _coerce(k, v):
    t = EDITABLE[k]
    if t is bool: return bool(v)
    if t in (int, float): return t(v)
    if isinstance(t, tuple): assert v in t; return v
    if t == "time": hm(v); return v
    assert v == "now" or hm(v) >= 0; return v

def save_settings():
    try: _atomic(SETTINGS_PATH, dict({k: globals()[k] for k in EDITABLE}, AUTO_REENTER_ON=AUTO))
    except Exception as e: print("could not save settings:", e)

def load_settings():
    global AUTO
    if not os.path.exists(SETTINGS_PATH): return
    try: saved = json.load(open(SETTINGS_PATH))
    except Exception as e: print("settings.json unreadable, using defaults:", e); return
    n = 0
    for k, v in saved.items():
        if k == "AUTO_REENTER_ON": AUTO = bool(v); continue
        if k not in EDITABLE: continue
        try: globals()[k] = _coerce(k, v); n += 1
        except Exception: print("ignored bad saved value for", k, v)
    print(f"Loaded {n} saved settings from settings.json")

def margin_used():
    """Paper margin = spread width x qty for every OPEN trade (defined-risk, i.e. roughly the worst-case loss)."""
    return sum((p_.get("margin") or (p_.get("width") or 0)*(p_.get("qty") or 0)) for p_ in list(POSITIONS) if p_.get("status") == "OPEN")

def acct_available():
    a = SNAP.get("account") if isinstance(SNAP, dict) else None
    return ACCOUNT["start"] + ACCOUNT["realized"] + (a["open"] if a else 0.0) - margin_used()

def load_account():
    if os.path.exists(ACCOUNT_PATH):
        try: ACCOUNT.update(json.load(open(ACCOUNT_PATH))); return
        except Exception: pass
    eq = float(START_EQUITY)
    try:
        raw = input(f"Starting paper equity in Rs [{START_EQUITY:,}] (press Enter for default): ").strip().replace(",", "")
        if raw: eq = float(raw)
    except Exception: pass
    if not eq > 0: eq = float(START_EQUITY)
    past = 0.0
    try:
        if os.path.exists(LEDGER): past = float(pd.read_csv(LEDGER)["net_pnl"].astype(float).sum())   # keep old trades consistent with the account
    except Exception: pass
    ACCOUNT.update(start=eq, realized=past, peak=eq+max(past, 0), maxdd=0.0, created=str(now_ist()), resets=0); save_account()
    print(f"Paper account created: starting equity Rs{eq:,.0f}" + (f" (+ Rs{past:,.0f} from existing trades.csv)" if past else ""))

def clear_all(eq):
    """Delete ALL history (open/pending trades are removed, NOT squared off) and start a fresh account with `eq` equity."""
    n_open = sum(1 for p_ in POSITIONS if p_.get("status") == "OPEN")
    POSITIONS.clear(); SEL["pid"] = None; PID[0] = 0; GLOG.clear()
    victims = [LEDGER, EVENTS, AUDIT, STATE_PATH, STATE_PATH + ".bak"] + [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR) if "_old_" in f and f.endswith(".csv")]
    for f in victims:
        try:
            if os.path.exists(f): os.remove(f)
        except Exception as e: print("could not delete", f, e)
    session.clear(); session.update(date=str(now_ist().date()), realized=0.0, n=0); save_session()
    ACCOUNT.update(start=float(eq), realized=0.0, peak=float(eq), maxdd=0.0, created=str(now_ist()), resets=ACCOUNT.get("resets", 0)+1); save_account()
    with TLOCK: TRIGGERS.clear(); save_trigs()
    make_idle(); save_state()
    log(f"FRESH START: all history deleted ({n_open} open trade(s) discarded, not squared off). New paper equity Rs{eq:,.0f}")
    say(f"Everything cleared. Fresh paper account with Rs{eq:,.0f}.")

# ------------------------------------------------------------------ candles (built live from ticks + seeded from history)
def tick_candle(key, px, ts):
    m = int(ts//60*60)
    with clock:
        d = CANDLES.setdefault(key, {}); c = d.get(m)
        if c is None: d[m] = [px, px, px, px, 0.0]
        else:
            c[1] = max(c[1], px); c[2] = min(c[2], px); c[3] = px

def bucket(m, tf):                    # returns IST-shifted bucket start, aligned to 09:15
    s = m+SHIFT; start = s//86400*86400+33300
    return start+((s-start)//(tf*60))*tf*60

def aggregate(d, tf, tail=False):
    ms = sorted(d); out = []
    if tail and ms: ms = [m for m in ms if m >= ms[-1]-3*tf*60-60]
    cur = None
    for m in ms:
        x = d[m]; o, h, l, c = x[:4]; v = x[4] if len(x) > 4 else 0.0; b = bucket(m, tf)
        if cur and cur[0] == b: cur[2] = max(cur[2], h); cur[3] = min(cur[3], l); cur[4] = c; cur[5] += v
        else: cur = [b, o, h, l, c, v]; out.append(cur)
    return [dict(time=int(x[0]), open=x[1], high=x[2], low=x[3], close=x[4], volume=x[5]) for x in out]

LAST_VTT = {}
def tick_volume(key, vtt, ts):
    """Feed gives cumulative volume traded today (vtt). Per-minute volume = difference between ticks."""
    prev = LAST_VTT.get(key); LAST_VTT[key] = vtt
    if prev is None or vtt <= prev: return None
    m = int(ts//60*60)
    with clock:
        c = CANDLES.setdefault(key, {}).get(m)
        if c is not None:
            if len(c) < 5: c.append(0.0)
            c[4] += vtt-prev
    return vtt-prev

DELTA = {}; LAST_PX = {}; LAST_SGN = {}
def tick_delta(key, px, dv, ts):
    """Estimated order-flow delta per minute. The feed has no aggressor flag, so each chunk of new volume is signed
    buy (+) if it printed at/above the ask or the price ticked up, sell (-) if at/below the bid or the price ticked down."""
    last = LAST_PX.get(key); LAST_PX[key] = px
    if not dv: return
    with lock: qq = dict(quotes.get(key) or {})
    bid, ask = qq.get("bid"), qq.get("ask")
    if ask and px >= ask: sg = 1
    elif bid and px <= bid: sg = -1
    elif last is not None and px > last: sg = 1
    elif last is not None and px < last: sg = -1
    else: sg = LAST_SGN.get(key, 0)
    if sg: LAST_SGN[key] = sg
    m = int(ts//60*60)
    with clock: dd = DELTA.setdefault(key, {}); dd[m] = dd.get(m, 0.0)+sg*dv

def fetch_hist(key, days=6):
    rows = []; k = urlq(key, safe="")
    urls = [f"https://api.upstox.com/v3/historical-candle/{k}/minutes/1/{date.today()}/{date.today()-timedelta(days=days)}",
            f"https://api.upstox.com/v3/historical-candle/intraday/{k}/minutes/1"]
    for u in urls:
        try:
            r = requests.get(u, headers=HDR, timeout=25)
            if r.ok: rows += r.json().get("data", {}).get("candles", [])
        except Exception as e: print("history fetch failed:", e)
    return rows

def merge_hist(key, rows):
    with clock:
        d = CANDLES.setdefault(key, {})
        for r in rows:
            try:
                m = int(datetime.fromisoformat(r[0]).timestamp()//60*60)
                if m not in d: d[m] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]), (float(r[5]) if len(r) > 5 and r[5] is not None else 0.0)]
            except Exception: pass

def seed_spread(sk, lk):
    sp = f"SP|{sk}|{lk}"
    with clock:
        a, b = CANDLES.get(sk, {}), CANDLES.get(lk, {}); d = CANDLES.setdefault(sp, {})
        for m in set(a) & set(b):
            if m in d: continue
            o = a[m][0]-b[m][0]; c = a[m][3]-b[m][3]; d[m] = [o, max(o, c), min(o, c), c, 0.0]

def seed_async(*keys):
    def run():
        for k in keys:
            if not k or k in SEEDED: continue
            SEEDED.add(k)
            try: merge_hist(k, fetch_hist(k))
            except Exception as e: print("seed error:", e)
        try: seed_spread(*CUR_LEGS)
        except Exception: pass
    threading.Thread(target=run, daemon=True).start()

# ------------------------------------------------------------------ contracts
def find_fut_api():
    """Fallback: ask Upstox's instrument search for the current-month NIFTY future."""
    for exp in ("current_month", "next_month"):
        try:
            r = requests.get("https://api.upstox.com/v2/instruments/search", headers=HDR, timeout=15,
                             params={"query": UNDERLYING_NAME, "exchanges": "NSE", "segments": "FO", "instrument_types": "FUT", "expiry": exp})
            for x in (r.json().get("data") or []):
                if x.get("instrument_key") and str(x.get("trading_symbol", x.get("name", ""))).upper().startswith(UNDERLYING_NAME): return x["instrument_key"]
        except Exception as e: print("Upstox futures search failed:", e)
    return ""

def load_opts():
    global opts, LOT
    today = date.today().strftime("%Y%m%d"); path = f"{DATA_DIR}/instruments_{today}.csv.gz"
    for f in os.listdir(DATA_DIR):
        if f.startswith("instruments_") and f != os.path.basename(path): os.remove(os.path.join(DATA_DIR, f))
    if not os.path.exists(path):
        print("Downloading instrument list (once a day)…")
        with requests.get("https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz", stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(path, "wb") as f: shutil.copyfileobj(r.raw, f)
    inst = pd.read_csv(path, low_memory=False, usecols=lambda c: c in {"exchange", "name", "option_type", "expiry", "lot_size", "instrument_key", "instrument_type", "tradingsymbol"})
    opts = inst[(inst.exchange == "NSE_FO") & (inst.name == UNDERLYING_NAME) & (inst.option_type.isin(["PE", "CE"]))].copy()
    assert len(opts) > 0, "No Nifty options found in the instrument file"
    n = pd.to_numeric(opts["expiry"], errors="coerce")
    opts["exp"] = (pd.to_datetime(n, unit="ms", utc=True).dt.tz_convert("Asia/Kolkata").dt.date if n.notna().all()
                   else pd.to_datetime(opts["expiry"]).dt.date)
    LOT = int(opts.lot_size.iloc[0]); print("Lot size:", LOT)
    try:
        e = inst[(inst.exchange == "NSE_EQ") & (inst.tradingsymbol.isin(list(NIFTY_WEIGHTS)))].drop_duplicates("tradingsymbol")
        EQ_KEYS.update(dict(zip(e.tradingsymbol, e.instrument_key))); print("Heavyweight stocks found:", len(EQ_KEYS), "of", len(NIFTY_WEIGHTS))
    except Exception as ex: print("Could not find heavyweight stocks (index contribution signal will show NO DATA):", ex)
    try:
        f = inst[(inst.exchange == "NSE_FO") & (inst.name == UNDERLYING_NAME) & (inst.instrument_type == "FUT")].copy()
        n2 = pd.to_numeric(f["expiry"], errors="coerce")
        f["exp"] = (pd.to_datetime(n2, unit="ms", utc=True).dt.tz_convert("Asia/Kolkata").dt.date if n2.notna().all() else pd.to_datetime(f["expiry"]).dt.date)
        f = f[f.exp > now_ist().date()].sort_values("exp"); globals()["FUT_KEY"] = f.instrument_key.iloc[0]; print("Futures:", FUT_KEY, f.exp.iloc[0])
    except Exception as e:
        print("Futures not found in the instrument file:", e, "-> asking the Upstox search API")
        k = find_fut_api()
        if k: globals()["FUT_KEY"] = k; print("Futures (via Upstox API):", k)
        else: print("NIFTY futures contract not found: the VWAP and volume signals will show NO DATA")

def choose_expiry():
    if EXPIRY_OVERRIDE: return date.fromisoformat(EXPIRY_OVERRIDE)
    today = now_ist().date(); exps = sorted(e for e in set(opts.exp) if e >= today)
    if EXPIRY_TYPE == "monthly":
        by = {}
        for e in exps: by[(e.year, e.month)] = max(by.get((e.year, e.month), e), e)
        exps = sorted(by.values())
    for e in exps:
        if (e-today).days >= MIN_DTE_ENTRY: return e
    raise RuntimeError("No expiry found")

TRADE_EXP = [None]        # option expiry picked on the chart page for NEW trades (None = the automatic one, EXPIRY0)
def exp_choices():
    today = now_ist().date()
    try: return sorted(e for e in set(opts.exp) if e >= today)[:12]
    except Exception: return [EXPIRY0] if EXPIRY0 else []
def trade_expiry():
    e = TRADE_EXP[0]
    return e if e and e >= now_ist().date() else EXPIRY0

def fetch_chain(expiry, side="PUT"):
    r = requests.get("https://api.upstox.com/v2/option/chain", headers=HDR,
                     params={"instrument_key": UNDERLYING_KEY, "expiry_date": str(expiry)}, timeout=20)
    r.raise_for_status(); rows = []
    for x in r.json()["data"]:
        p = x.get("call_options" if side == "CALL" else "put_options") or {}; md_ = p.get("market_data") or {}; og = p.get("option_greeks") or {}
        rows.append(dict(strike=x["strike_price"], spot=x.get("underlying_spot_price"), key=p.get("instrument_key"),
                         ltp=md_.get("ltp"), delta=og.get("delta")))
    ch = pd.DataFrame(rows).dropna(subset=["key"])
    for c in ("strike", "spot", "ltp", "delta"): ch[c] = pd.to_numeric(ch[c], errors="coerce")
    return ch.sort_values("strike").reset_index(drop=True)

def pick_legs(ch, side="PUT"):
    g = lambda n: globals()[("CALL_" if side == "CALL" else "")+n]; spot = ch.spot.dropna().iloc[0]; up = side == "CALL"
    if g("SHORT_MODE") == "atm": s_ = ch.iloc[(ch.strike-spot).abs().argsort().iloc[0]]
    else:
        c = ch[((ch.strike >= spot) if up else (ch.strike <= spot)) & (ch.ltp > 0)].dropna(subset=["delta"])
        s_ = c.iloc[(c.delta.abs()-g("SHORT_DELTA")).abs().argsort().iloc[0]]
    if g("HEDGE_MODE") == "delta":
        c = ch[((ch.strike > s_.strike) if up else (ch.strike < s_.strike)) & (ch.ltp > 0)].dropna(subset=["delta"])
        l = c.iloc[(c.delta.abs()-g("HEDGE_DELTA")).abs().argsort().iloc[0]]
    else: l = ch.iloc[(ch.strike-(s_.strike+(1 if up else -1)*g("HEDGE_POINTS"))).abs().argsort().iloc[0]]
    return dict(side=side, short=dict(key=s_.key, strike=float(s_.strike), delta=float(s_.delta)),
                long=dict(key=l.key, strike=float(l.strike), delta=float(l.delta)), spot=float(spot))

# ------------------------------------------------------------------ live feed
def _f(x):
    try: return None if x in (None, "") else float(x)
    except Exception: return None


LAST_WS = 0.0; REST_MODE = False
def LAST_WS_SET():
    global LAST_WS; LAST_WS = time.time()

def ingest(key, q):
    q["t"] = time.time()
    with lock: quotes.setdefault(key, {}).update(q)
    if q.get("ltp") is not None:
        tick_candle(key, q["ltp"], q["t"])
        dv = tick_volume(key, q["vtt"], q["t"]) if q.get("vtt") is not None else None
        if FUT_KEY and key == FUT_KEY: tick_delta(key, q["ltp"], dv, q["t"])
        if FUT_KEY and key == FUT_KEY and SIG_RF_VWAP <= 0: _vwap_tick(q["t"])         # signal 1: re-checked on every single futures tick
        for sk_, lk_ in ACTIVE_PAIRS:
            if key in (sk_, lk_):
                with lock: a = (quotes.get(sk_) or {}).get("ltp"); b = (quotes.get(lk_) or {}).get("ltp")
                if a is not None and b is not None: tick_candle(f"SP|{sk_}|{lk_}", a-b, q["t"])

def rest_poller():
    """Fallback when the websocket is refused/closed: polls Upstox REST (quotes every 3s, greeks every 9s)."""
    global REST_MODE, feed_open
    time.sleep(10); n = 0
    while not ENGINE_STOP.is_set():
        if time.time()-LAST_WS > 8:
            if not REST_MODE: REST_MODE = True; say("Websocket not delivering data -> using REST polling fallback (3s refresh)")
            keys = feed_keys()
            try:
                r = requests.get("https://api.upstox.com/v2/market-quote/quotes", headers=HDR, params={"instrument_key": ",".join(keys)}, timeout=10)
                if r.ok:
                    for k, v in (r.json().get("data") or {}).items():
                        key = v.get("instrument_token") or k.replace(":", "|"); q = {"ltp": _f(v.get("last_price")), "vtt": _f(v.get("volume"))}
                        d = v.get("depth") or {}
                        if d.get("buy"): q["bid"] = _f(d["buy"][0].get("price"))
                        if d.get("sell"): q["ask"] = _f(d["sell"][0].get("price"))
                        ingest(key, {a: b for a, b in q.items() if b is not None})
                    feed_open = True
                elif n % 20 == 0: print("REST quotes failed:", r.status_code, r.text[:150])
                if n % 3 == 0:
                    r = requests.get("https://api.upstox.com/v3/market-quote/option-greek", headers=HDR, params={"instrument_key": ",".join(CUR_LEGS)}, timeout=10)
                    if r.ok:
                        for k, v in (r.json().get("data") or {}).items():
                            key = v.get("instrument_token") or k.replace(":", "|"); q = {x: _f(v.get(x)) for x in ("delta", "theta", "gamma", "vega")}
                            iv = _f(v.get("iv"))
                            if iv is not None: q["iv"] = iv*100 if iv < 3 else iv
                            ingest(key, {a: b for a, b in q.items() if b is not None})
            except Exception as e:
                if n % 20 == 0: print("REST poll error:", e)
            n += 1
        time.sleep(3)

# ------------------------------------------------------------------ TICK RECORDER (for replay and backtesting)
TICKS_DIR = os.path.join(DATA_DIR, "ticks"); os.makedirs(TICKS_DIR, exist_ok=True)
TICKQ = collections.deque(); KEYLABEL = {}; REC_KEYS = {}; REC_SIG_LAST = [0.0]
TICK_COLS = ["ts", "key", "ltp", "ltq", "ltt", "cp", "bid", "bidq", "ask", "askq", "atp", "vtt", "oi", "tbq", "tsq", "delta", "theta", "gamma", "vega", "iv", "bids", "asks"]
def _ist_day(ts): return datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d")
def record_tick(key, q, src, ltpc, lv, top):
    top = top or {}; f = _f
    dep = lambda p_, q_: "|".join(f"{x.get(p_)}:{x.get(q_)}" for x in lv[:5])
    TICKQ.append((q["t"], key, q.get("ltp"), f(ltpc.get("ltq")), ltpc.get("ltt"), q.get("cp"), q.get("bid"), f(top.get("bidQ")), q.get("ask"), f(top.get("askQ")), f(src.get("atp")),
                  q.get("vtt"), f(src.get("oi")), f(src.get("tbq")), f(src.get("tsq")), q.get("delta"), q.get("theta"), q.get("gamma"), q.get("vega"), q.get("iv"), dep("bidP", "bidQ"), dep("askP", "askQ")))
def flush_ticks():
    rows = []
    while TICKQ: rows.append(TICKQ.popleft())
    if not rows: return
    by = {}
    for r in rows: by.setdefault(_ist_day(r[0]), []).append(r)
    for day, rs in by.items():
        path = f"{TICKS_DIR}/ticks_{day}.csv"; new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new: w.writerow(TICK_COLS)
            w.writerows(rs)
        ks = REC_KEYS.setdefault(day, set()); n0 = len(ks); ks.update(r[1] for r in rs)
        if len(ks) != n0 or new:
            lab = {UNDERLYING_KEY: "NIFTY SPOT"}; lab.update({FUT_KEY: "NIFTY FUT"} if FUT_KEY else {}); lab.update({v: k for k, v in EQ_KEYS.items()}); lab.update(KEYLABEL)
            _atomic(f"{TICKS_DIR}/keys_{day}.json", {k: lab.get(k, k) for k in ks})
def tick_writer():
    while True:
        time.sleep(1)
        try: flush_ticks()
        except Exception as e: print("tick recorder error:", e)
def record_oi(rows, wall):
    path = f"{TICKS_DIR}/oi_{_ist_day(wall)}.csv"; new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new: w.writerow(["ts", "expiry", "strike", "call_oi", "call_prev_oi", "put_oi", "put_prev_oi"])
        w.writerows([(round(wall, 1), str(EXPIRY0), *r) for r in rows])
def record_signals(wall, qs, items, v):
    if wall-REC_SIG_LAST[0] < 60: return
    REC_SIG_LAST[0] = wall; path = f"{TICKS_DIR}/signals_{_ist_day(wall)}.csv"; new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new: w.writerow(["ts", "spot", "futures", "verdict"]+[i["key"] for i in items])
        w.writerow([round(wall, 1), spot_of(qs), (qs.get(FUT_KEY) or {}).get("ltp"), v]+[i["state"] for i in items])
def label_keys():
    for p_ in list(POSITIONS):
        src = p_ if p_.get("status") != "WAITING" else p_.get("plan", {})
        if not src.get("short"): continue
        ot = p_.get("opt") if p_.get("kind") == "SINGLE" else ("PE" if p_.get("side") == "PUT" else "CE")
        for leg in (src["short"], src["long"]):
            if ot != "FUT": KEYLABEL[leg["key"]] = f"{leg['strike']:.0f} {ot}"

def on_message(m):
    try:
        for key, f in (m.get("feeds") or {}).items():
            ff = f.get("fullFeed") or {}
            src = ff.get("marketFF") or ff.get("indexFF") or f.get("firstLevelWithGreeks") or f
            ltpc = src.get("ltpc") or f.get("ltpc") or {}
            q = {}
            if _f(ltpc.get("ltp")) is not None: q["ltp"] = _f(ltpc["ltp"])
            if _f(ltpc.get("cp")) is not None: q["cp"] = _f(ltpc["cp"])
            lv = ((src.get("marketLevel") or {}).get("bidAskQuote") or []); fd = src.get("firstDepth")
            top = lv[0] if lv else fd
            if top: q["bid"] = _f(top.get("bidP")); q["ask"] = _f(top.get("askP"))
            og = src.get("optionGreeks")
            if og:
                for k in ("delta", "theta", "gamma", "vega"): q[k] = _f(og.get(k))
            if _f(src.get("iv")) is not None:
                v = _f(src["iv"]); q["iv"] = v*100 if v < 3 else v
            if _f(src.get("vtt")) is not None: q["vtt"] = _f(src["vtt"])
            if len(RAW) < 4 and "|" in key and key != UNDERLYING_KEY: RAW.append(f)
            if q:
                LAST_WS_SET(); ingest(key, q)
                if RECORD_TICKS and q.get("ltp") is not None and key not in EQ_KEYS.values(): record_tick(key, q, src, ltpc, lv, top)
    except Exception as e: print("tick parse error:", e)

_reconnecting = False; _backoff = 5
def _connect(keys):
    global streamer, feed_open, _reconnecting
    try:
        if streamer: streamer.disconnect()
    except Exception: pass
    feed_open = False
    cfg = upstox_client.Configuration(); cfg.access_token = TOKEN
    st_ = upstox_client.MarketDataStreamerV3(upstox_client.ApiClient(cfg), list(keys), "full")
    def _open(*a):
        global feed_open, _backoff, _reconnecting
        feed_open = True; _backoff = 5; _reconnecting = False; print("WebSocket connected")
    def _lost(*a):
        global _reconnecting
        if a and not isinstance(a[0], (int, type(None))): print("WS error:", a[0])
        if ENGINE_STOP.is_set() or _reconnecting: return
        _reconnecting = True; threading.Thread(target=_retry, daemon=True).start()
    st_.on("open", _open); st_.on("message", on_message); st_.on("error", _lost); st_.on("close", _lost)
    streamer = st_; st_.connect()

def _retry():
    global _backoff, _reconnecting
    wait = _backoff; _backoff = min(_backoff*2, 60)
    print(f"WebSocket down. Fresh reconnect in {wait}s (new authorisation each time)…"); time.sleep(wait)
    if ENGINE_STOP.is_set(): return
    _reconnecting = False
    try: _connect(feed_keys())
    except Exception as e: print("reconnect failed:", e); _lost()

def start_feed(keys): _connect(keys)

def change_subs(old_keys, new_keys):
    try:
        if old_keys: streamer.unsubscribe(list(old_keys))
        if new_keys: streamer.subscribe(list(new_keys), "full")
    except Exception as e: print("resubscribe failed:", e)

START_T = time.time()
def feed_watchdog():
    """WebSocket-only self-healing (no REST). The old reconnect logic only reacted to socket errors/close events, so a
    silent stall (option ticks still arriving, Nifty index ticks gone) was never noticed. Here: if the index stops
    ticking for 20s while the market is open -> 1) re-subscribe the index, 2) still stale -> brand-new websocket."""
    global _reconnecting
    stage = 0; last_act = 0.0; time.sleep(20)
    while not ENGINE_STOP.is_set():
        time.sleep(5)
        try:
            nw, wall = now_ist(), time.time()
            if not market_open(nw): stage = 0; continue
            with lock: t_idx = (quotes.get(UNDERLYING_KEY) or {}).get("t")
            idx_age = (wall-START_T) if t_idx is None else wall-t_idx
            ws_age = wall-LAST_WS if LAST_WS else wall-START_T
            if idx_age <= 20 and ws_age <= 30: stage = 0; continue
            if wall-last_act < 25: continue
            last_act = wall
            if stage == 0 and ws_age <= 30:
                say(f"Nifty spot feed silent for {idx_age:.0f}s -> re-subscribing spot on the websocket")
                log(f"WATCHDOG: spot stale {idx_age:.0f}s, re-subscribing {UNDERLYING_KEY}")
                try: streamer.unsubscribe([UNDERLYING_KEY]); time.sleep(1); streamer.subscribe([UNDERLYING_KEY], "full")
                except Exception as e: print("watchdog resubscribe failed:", e)
                stage = 1
            else:
                say(f"Websocket stale (spot {idx_age:.0f}s, any data {ws_age:.0f}s) -> opening a fresh websocket connection")
                log(f"WATCHDOG: fresh websocket (spot stale {idx_age:.0f}s, any data {ws_age:.0f}s)")
                _reconnecting = True
                try: _connect(feed_keys())
                except Exception as e: _reconnecting = False; print("watchdog reconnect failed:", e)
                stage = 0
        except Exception as e: print("watchdog error:", e)

def set_cur_legs():
    global CUR_LEGS
    src = state if state.get("status") != "WAITING" else state["plan"]
    CUR_LEGS = (src["short"]["key"], src["long"]["key"]); seed_async(UNDERLYING_KEY, FUT_KEY, *CUR_LEGS); refresh_pairs()

# ------------------------------------------------------------------ strategy engine
def new_state(plan):
    return dict(status="WAITING", side=plan.get("side", "PUT"), expiry=str(plan.get("expiry") or EXPIRY0), lot=LOT, qty=(CALL_LOTS if plan.get("side") == "CALL" else LOTS)*LOT, plan=plan, hist=[], log=[])


LEDGER_COLS = ["trade_id","entry_time","exit_time","duration_min","held_days","expiry","dte_at_entry","lots","qty",
  "short_strike","long_strike","width","entry_short_px_bid","entry_long_px_ask","entry_credit_pts","entry_credit_rs",
  "exit_short_px_ask","exit_long_px_bid","exit_debit_pts","entry_spot","exit_spot",
  "entry_short_delta","entry_long_delta","entry_short_iv","entry_short_theta","exit_short_delta","exit_short_iv",
  "gross_pnl","entry_charges","exit_charges","total_charges","net_pnl","net_pct_of_credit","best_net_pnl","worst_net_pnl",
  "target_rs","buffer_rs","exit_reason","setting_target_pct","setting_buffer_x","setting_buffer_delta","setting_max_hold_days",
  "setting_exit_time","setting_short_mode","setting_short_delta","setting_hedge_mode","setting_hedge_delta","setting_hedge_points","setting_auto_reenter"]
EVENT_COLS = ["time","trade_id","event","expiry","short_strike","long_strike","short_px","long_px","spot","short_delta","short_iv","net_pnl","charges","reason","qty"]
EVENTS = f"{DATA_DIR}/trade_events.csv"

def append_csv(path, cols, row):
    pd.DataFrame([row]).reindex(columns=cols).to_csv(path, mode="a", header=not os.path.exists(path), index=False)

def ensure_ledger():
    for path, cols in ((LEDGER, LEDGER_COLS), (EVENTS, EVENT_COLS)):
        if os.path.exists(path):
            try: head = list(pd.read_csv(path, nrows=0).columns)
            except Exception: head = []
            if head != cols:
                os.replace(path, path.replace(".csv", f"_old_{int(time.time())}.csv")); print("Old-format", os.path.basename(path), "archived")

def spot_now():
    with lock: return (quotes.get(UNDERLYING_KEY) or {}).get("ltp")


TRIGGERS = []; TLOCK = threading.Lock(); TRIG_PATH = f"{DATA_DIR}/triggers.json"
def save_trigs():
    try: _atomic(TRIG_PATH, TRIGGERS)
    except Exception as e: print("could not save triggers:", e)

def series_price(kind, qs):
    src = state if state.get("status") not in (None, "WAITING") else state.get("plan", {})
    if kind == "spot": return spot_of(qs)
    sk, lk = (src.get("short") or {}).get("key"), (src.get("long") or {}).get("key")
    a, b = (qs.get(sk) or {}).get("ltp"), (qs.get(lk) or {}).get("ltp")
    if kind == "short": return a
    if kind == "long": return b
    return (a-b) if a is not None and b is not None else None

def eval_triggers(nw, wall, qs):
    """Chart trigger lines: fires EXIT / ENTER / ALERT when the live price CROSSES the line.
    An EXIT line belongs to the trade that was selected on the chart when the line was drawn, and closes ONLY that trade."""
    if not TRIGGERS: return
    age = feed_age(qs, wall); alive = market_open(nw) and age is not None and age < 15
    for t in list(TRIGGERS):
        if t["status"] != "armed": continue
        pos = None
        if t["act"] == "EXIT":
            pid = t.get("pid") if t.get("pid") is not None else SEL["pid"]      # old lines without a trade fall back to the selected one
            pos = next((p_ for p_ in POSITIONS if p_.get("pid") == pid), None)
            if pos is None or pos["status"] == "CLOSED":
                t["status"] = "expired"; log(f"Trigger #{t['id']} expired: trade #{pid} is no longer open"); save_trigs(); continue
            if pos["status"] != "OPEN": continue                                 # pending trade: the line waits until it is open
        with (use(pos) if pos else contextlib.nullcontext()):
            if t["sig"] != series_info(t["series"])[0]: continue
            px = series_price(t["series"], qs)
            if px is None: continue
            last = t.get("last"); t["last"] = px
            if not alive or last is None: continue
            lv = t["price"]; up = last < lv <= px; dn = last > lv >= px
            if not ((t["dir"] == "up" and up) or (t["dir"] == "down" and dn) or (t["dir"] == "any" and (up or dn))): continue
            if time.time()-t.get("fired_at", 0) < 10: continue
            name = f"{t['label']} crossed {lv:,.2f} {'up' if up else 'down'}"
            if t["act"] == "EXIT":
                m = mark(qs)
                if not m: continue
                do_exit(nw, m, f"MANUAL: trigger line, {name}")
                say(f"TRIGGER EXIT (trade #{pos['pid']} only): {name}. Net {money(m['net'])}")
            elif t["act"] == "ENTER":
                if n_live() >= MAX_OPEN_TRADES: continue
                cmdq.put("ENTER"); say(f"TRIGGER ENTER: {name}")
            else: say(f"ALERT: {name}")
            t["fired_at"] = time.time(); t["fires"] = t.get("fires", 0)+1
            if t["once"]: t["status"] = "fired"
            log(f"Trigger #{t['id']} {t['act']} fired: {name}"); save_trigs()


def charges_fut(orders):
    b = txn = sebi = stt = stamp = 0.0
    for side, px, q in orders:
        t = px*q; b += min(20.0, t*0.0003); txn += t*0.0000183; sebi += t*1e-6
        if side == "S": stt += t*0.0002
        else: stamp += t*0.00002
    return b+txn+sebi+0.18*(b+txn+sebi)+stt+stamp
def charges_for(st, orders): return charges_fut(orders) if st.get("opt") == "FUT" else charges(orders)

def mark_single(qs):
    st = state; s = st["short"]; q = qs.get(s["key"]) or {}
    if not q.get("ltp"): return None
    d = st["dirn"]; xp = px_exit_long(q) if d > 0 else px_exit_short(q); n = st["qty"]
    gross = d*(xp-s["entry_px"])*n; exch = charges_for(st, [("S" if d > 0 else "B", xp, n)])
    return dict(xs=xp, xl=xp, gross=gross, exit_ch=exch, net=gross-st["entry_charges"]-exch, qS=q, qL=q)

def do_entry_single(nw, qs):
    p = state["plan"]; n = state["qty"]; leg = p["short"]; d = state["dirn"]; qq = qs[leg["key"]]
    ep = px_entry_long(qq) if d > 0 else px_entry_short(qq); ech = charges_for(state, [("B" if d > 0 else "S", ep, n)])
    tid = f"{'F' if state['opt'] == 'FUT' else 'O'}{'B' if d > 0 else 'S'}-{nw:%Y%m%d-%H%M%S}-{state.get('pid', 0)}"
    state.update(status="OPEN", trade_id=tid, entry_time=str(nw), entry_date=str(nw.date()), entry_ts=time.time(), entry_spot=spot_of(qs), entry_charges=ech,
                 credit=ep, width=0.0, entry_dte=(exp_date()-nw.date()).days, entry_g={}, max_pnl=0.0, min_pnl=0.0, target=(FUT_TARGET_PTS if state["opt"] == "FUT" else NK_TARGET_PTS)*n, buffer=(FUT_STOP_PTS if state["opt"] == "FUT" else NK_STOP_PTS)*n,
                 hist=[], short=dict(leg, entry_px=ep), long=dict(leg, entry_px=ep))
    state.pop("exit", None); state.pop("_booked", None)
    log(f"ENTRY {tid}: {'BUY' if d > 0 else 'SELL'} {state['ilabel']} @ {ep:.2f} x {n} | charges Rs{ech:,.0f}")
    append_csv(EVENTS, EVENT_COLS, dict(time=str(nw), trade_id=tid, event="ENTRY", expiry=state["expiry"], short_strike=leg["strike"], long_strike=leg["strike"],
               short_px=ep, long_px=ep, spot=spot_of(qs), net_pnl=0, charges=round(ech, 2), reason="", qty=n)); save_state()

def do_exit_single(nw, m, reason):
    st = state; s = st["short"]; n = st["qty"]; d = st["dirn"]; spot = spot_now(); tid = st.get("trade_id", "T?")
    st["status"] = "CLOSED"; st["exit"] = dict(time=str(nw), ts=time.time(), reason=reason, short_px=m["xs"], long_px=m["xl"], gross=m["gross"], charges=st["entry_charges"]+m["exit_ch"], net=m["net"])
    log(f"EXIT {tid} ({reason}): {'SELL' if d > 0 else 'BUY'} {st['ilabel']} @ {m['xs']:.2f} | net {money(m['net'])}")
    best, worst = max(st.get("max_pnl", 0), m["net"]), min(st.get("min_pnl", 0), m["net"]); ep = s["entry_px"]
    append_csv(LEDGER, LEDGER_COLS, dict(trade_id=tid, entry_time=st["entry_time"], exit_time=str(nw), duration_min=round((time.time()-st.get("entry_ts", time.time()))/60, 1),
        held_days=held_days(nw), expiry=st["expiry"], dte_at_entry=st.get("entry_dte"), lots=n//max(st.get("lot", LOT), 1), qty=n, short_strike=s["strike"], long_strike=s["strike"], width=0,
        entry_short_px_bid=ep if d < 0 else None, entry_long_px_ask=ep if d > 0 else None, entry_credit_pts=round(ep*(1 if d < 0 else -1), 2), entry_credit_rs=round(ep*n*(1 if d < 0 else -1), 2),
        exit_short_px_ask=m["xs"] if d < 0 else None, exit_long_px_bid=m["xl"] if d > 0 else None, entry_spot=st.get("entry_spot"), exit_spot=spot,
        gross_pnl=round(m["gross"], 2), entry_charges=round(st["entry_charges"], 2), exit_charges=round(m["exit_ch"], 2), total_charges=round(st["exit"]["charges"], 2),
        net_pnl=round(m["net"], 2), best_net_pnl=round(best, 2), worst_net_pnl=round(worst, 2), target_rs=round(st["target"], 2), buffer_rs=round(st["buffer"], 2),
        exit_reason=reason, setting_target_pct=(FUT_TARGET_PTS if st.get("opt") == "FUT" else NK_TARGET_PTS), setting_buffer_x=(FUT_STOP_PTS if st.get("opt") == "FUT" else NK_STOP_PTS), setting_exit_time=EXIT_TIME, setting_auto_reenter=AUTO))
    append_csv(EVENTS, EVENT_COLS, dict(time=str(nw), trade_id=tid, event="EXIT", expiry=st["expiry"], short_strike=s["strike"], long_strike=s["strike"], short_px=m["xs"], long_px=m["xl"],
               spot=spot, net_pnl=round(m["net"], 2), charges=round(st["exit"]["charges"], 2), reason=reason, qty=n)); save_state()

def new_single(side, force=False, origin="manual"):
    d = 1 if side == "BUY" else -1; fut = TRADE_MODE == "futures"
    try:
        if fut:
            if not FUT_KEY: say("No futures contract was found, so futures trading is not available."); return None
            leg = dict(key=FUT_KEY, strike=0.0, delta=None); opt = "FUT"; lab = "NIFTY FUT"; sp = spot_now() or 0.0; px = (quotes.get(FUT_KEY) or {}).get("ltp") or sp
        else:
            opt = NK_OPT; _ex = trade_expiry(); ch = fetch_chain(_ex, "CALL" if opt == "CE" else "PUT"); sp = float(ch.spot.dropna().iloc[0])
            if NK_STRIKE_MODE == "atm": r = ch.iloc[(ch.strike-sp).abs().argsort().iloc[0]]
            else:
                c = ch[ch.ltp > 0].dropna(subset=["delta"]); r = c.iloc[(c.delta.abs()-NK_DELTA).abs().argsort().iloc[0]]
            leg = dict(key=r.key, strike=float(r.strike), delta=float(r.delta)); lab = f"{r.strike:.0f} {opt}"; px = float(r.ltp)
    except Exception as e: say(f"Could not set up the trade: {e}"); return None
    n = (FUT_LOTS if fut else NK_LOTS)*LOT; margin = px*n*FUT_MARGIN_PCT/100 if fut else (px*n if d > 0 else sp*n*0.15); have = acct_available()
    if margin > have: say(f"Not enough paper funds: this needs ~Rs{margin:,.0f}, available Rs{have:,.0f}."); return None
    bull = (d > 0) == (opt != "PE"); plan = dict(side="PUT" if bull else "CALL", short=leg, long=dict(leg), spot=sp)
    if not fut: plan["expiry"] = str(_ex)
    POSITIONS[:] = [p_ for p_ in POSITIONS if not p_.get("idle")]; PID[0] += 1; ns = new_state(plan)
    ns.update(pid=PID[0], origin=origin, kind="SINGLE", dirn=d, opt=opt, ilabel=lab, margin=margin, qty=n)
    if force: ns["force"] = True; ns["force_until"] = time.time()+45
    POSITIONS.append(ns); SEL["pid"] = ns["pid"]
    with use(ns): log(f"New direct {side} {lab} trade #{ns['pid']} planned")
    set_cur_legs(); return ns

def partial_exit(nw, qs, pid, lots, reason, auto=False):
    pos = next((p_ for p_ in POSITIONS if p_.get("pid") == pid and p_["status"] == "OPEN"), None)
    if not pos: say("That trade is not open."); return False
    with use(pos):
        lot = max(pos.get("lot", LOT), 1); tot = pos["qty"]//lot
        if tot < 2:
            if not auto: say(f"Trade #{pid} has only 1 lot, so it cannot be split. Use Exit instead.")
            return False
        k = max(1, min(int(lots), tot-1)); cq = k*lot; m = mark(qs)
        if not m: say("No live quotes yet, cannot price the partial exit."); return False
        f = cq/pos["qty"]; gross = m["gross"]*f; single = pos.get("kind") == "SINGLE"
        exch = charges_for(pos, [("S" if pos["dirn"] > 0 else "B", m["xs"], cq)]) if single else charges([("B", m["xs"], cq), ("S", m["xl"], cq)])
        ech = pos["entry_charges"]*f; net = gross-ech-exch; pos["np"] = pos.get("np", 0)+1; s_, l_ = pos["short"], pos["long"]; tid = f"{pos.get('trade_id', 'T?')}-P{pos['np']}"
        d = pos.get("dirn", 0); spot = spot_now()
        row = dict(trade_id=tid, entry_time=pos["entry_time"], exit_time=str(nw), duration_min=round((time.time()-pos.get("entry_ts", time.time()))/60, 1), held_days=held_days(nw),
                   expiry=pos["expiry"], dte_at_entry=pos.get("entry_dte"), lots=k, qty=cq, short_strike=s_["strike"], long_strike=l_["strike"], width=pos.get("width"),
                   entry_credit_pts=round(pos["credit"], 2) if not single else round(s_["entry_px"]*(1 if d < 0 else -1), 2), entry_spot=pos.get("entry_spot"), exit_spot=spot,
                   gross_pnl=round(gross, 2), entry_charges=round(ech, 2), exit_charges=round(exch, 2), total_charges=round(ech+exch, 2), net_pnl=round(net, 2), exit_reason=reason, setting_auto_reenter=AUTO)
        if single:
            row.update(entry_short_px_bid=s_["entry_px"] if d < 0 else None, entry_long_px_ask=s_["entry_px"] if d > 0 else None, exit_short_px_ask=m["xs"] if d < 0 else None, exit_long_px_bid=m["xl"] if d > 0 else None)
        else: row.update(entry_short_px_bid=s_["entry_px"], entry_long_px_ask=l_["entry_px"], exit_short_px_ask=m["xs"], exit_long_px_bid=m["xl"], exit_debit_pts=round(m["xs"]-m["xl"], 2))
        append_csv(LEDGER, LEDGER_COLS, row)
        append_csv(EVENTS, EVENT_COLS, dict(time=str(nw), trade_id=tid, event="PARTIAL", expiry=pos["expiry"], short_strike=s_["strike"], long_strike=l_["strike"], short_px=m["xs"], long_px=m["xl"],
                   spot=spot, net_pnl=round(net, 2), charges=round(ech+exch, 2), reason=reason, qty=cq))
        pos["qty"] -= cq; pos["entry_charges"] -= ech
        if single: pos["target"] *= (1-f); pos["buffer"] *= (1-f)
        if pos.get("margin"): pos["margin"] *= (1-f)
        if PARTIAL_MOVE_STOP and m["net"] > 0: pos["be_stop"] = True
        session["realized"] += net; ACCOUNT["realized"] += net; save_account(); save_session(); save_state()
        log(f"PARTIAL {tid}: closed {k} of {tot} lots ({reason}) | net {money(net)} | {tot-k} lot(s) still open")
        say(f"Partial exit on #{pid}: closed {k} lot(s), net {money(net)}. {tot-k} lot(s) still open." + (" Stop moved to breakeven." if pos.get("be_stop") else ""))
    return True

def mark(qs):
    if state.get("kind") == "SINGLE": return mark_single(qs)
    s, l = state["short"], state["long"]
    qS, qL = qs.get(s["key"]) or {}, qs.get(l["key"]) or {}
    if not qS.get("ltp") or not qL.get("ltp"): return None
    xs, xl = px_exit_short(qS), px_exit_long(qL); q = state["qty"]
    gross = ((s["entry_px"]-xs)+(xl-l["entry_px"]))*q
    exch = charges([("B", xs, q), ("S", xl, q)])
    return dict(xs=xs, xl=xl, gross=gross, exit_ch=exch, net=gross-state["entry_charges"]-exch, qS=qS, qL=qL)

def held_days(nw): return int(np.busday_count(date.fromisoformat(state["entry_date"]), nw.date()))

def do_entry(nw, qs):
    if state.get("kind") == "SINGLE": return do_entry_single(nw, qs)
    p = state["plan"]; q = state["qty"]
    qS, qL = qs[p["short"]["key"]], qs[p["long"]["key"]]
    sp, lp = px_entry_short(qS), px_entry_long(qL)
    if sp-lp <= 0: log("Skipped entry: net credit not positive (check quotes)"); return
    ech = charges([("S", sp, q), ("B", lp, q)]); credit = sp-lp; spot = spot_of(qs)
    gS, gL = leg_greeks(qS, p["short"]["strike"], spot), leg_greeks(qL, p["long"]["strike"], spot)
    tid = f"{'BC' if cur_side() == 'CALL' else 'BP'}-{nw:%Y%m%d-%H%M%S}-{state.get('pid', 0)}"
    state.update(status="OPEN", side=p.get("side", "PUT"), trade_id=tid, entry_time=str(nw), entry_date=str(nw.date()), entry_ts=time.time(), entry_spot=spot,
                 entry_charges=ech, credit=credit, width=abs(p["short"]["strike"]-p["long"]["strike"]), entry_dte=(exp_date()-nw.date()).days,
                 entry_g=dict(sd=gS["delta"], ld=gL["delta"], siv=gS["iv"], sth=gS["theta"]), max_pnl=0.0, min_pnl=0.0,
                 target=P("TARGET_PCT")/100*credit*q, buffer=P("BUFFER_LOSS_X")*credit*q, hist=[],
                 short=dict(p["short"], entry_px=sp), long=dict(p["long"], entry_px=lp))
    state.pop("exit", None); state.pop("_booked", None)
    log(f"ENTRY {tid}: SELL {p['short']['strike']:.0f}{OT()} @ {sp:.2f} (bid), BUY {p['long']['strike']:.0f}{OT()} @ {lp:.2f} (ask) | credit {credit:.2f} pts = Rs{credit*q:,.0f} | charges Rs{ech:,.0f}")
    append_csv(EVENTS, EVENT_COLS, dict(time=str(nw), trade_id=tid, event="ENTRY", expiry=state["expiry"], short_strike=p["short"]["strike"],
               long_strike=p["long"]["strike"], short_px=sp, long_px=lp, spot=spot, short_delta=gS["delta"], short_iv=gS["iv"],
               net_pnl=0, charges=round(ech, 2), reason="", qty=q))
    save_state()

def do_exit(nw, m, reason):
    if state.get("kind") == "SINGLE": return do_exit_single(nw, m, reason)
    st = state; s, l, q = st["short"], st["long"], st["qty"]; spot = spot_now()
    gS = leg_greeks(m["qS"], s["strike"], spot); g0 = st.get("entry_g") or {}
    st["status"] = "CLOSED"
    st["exit"] = dict(time=str(nw), ts=time.time(), reason=reason, short_px=m["xs"], long_px=m["xl"], gross=m["gross"],
                      charges=st["entry_charges"]+m["exit_ch"], net=m["net"])
    tid = st.get("trade_id", "T?")
    log(f"EXIT {tid} ({reason}): BUY {s['strike']:.0f}{OT()} @ {m['xs']:.2f}, SELL {l['strike']:.0f}{OT()} @ {m['xl']:.2f} | net {money(m['net'])}")
    best, worst = max(st.get("max_pnl", 0), m["net"]), min(st.get("min_pnl", 0), m["net"]); cr_rs = st["credit"]*q
    row = dict(trade_id=tid, entry_time=st["entry_time"], exit_time=str(nw), duration_min=round((time.time()-st.get("entry_ts", time.time()))/60, 1),
               held_days=held_days(nw), expiry=st["expiry"], dte_at_entry=st.get("entry_dte"), lots=q//max(st.get("lot", LOT), 1), qty=q,
               short_strike=s["strike"], long_strike=l["strike"], width=st.get("width"), entry_short_px_bid=s["entry_px"], entry_long_px_ask=l["entry_px"],
               entry_credit_pts=round(st["credit"], 2), entry_credit_rs=round(cr_rs, 2), exit_short_px_ask=m["xs"], exit_long_px_bid=m["xl"],
               exit_debit_pts=round(m["xs"]-m["xl"], 2), entry_spot=st.get("entry_spot"), exit_spot=spot,
               entry_short_delta=g0.get("sd"), entry_long_delta=g0.get("ld"), entry_short_iv=g0.get("siv"), entry_short_theta=g0.get("sth"),
               exit_short_delta=gS["delta"], exit_short_iv=gS["iv"], gross_pnl=round(m["gross"], 2), entry_charges=round(st["entry_charges"], 2),
               exit_charges=round(m["exit_ch"], 2), total_charges=round(st["exit"]["charges"], 2), net_pnl=round(m["net"], 2),
               net_pct_of_credit=round(m["net"]/cr_rs*100, 1) if cr_rs else None, best_net_pnl=round(best, 2), worst_net_pnl=round(worst, 2),
               target_rs=round(st["target"], 2), buffer_rs=round(st["buffer"], 2), exit_reason=reason, setting_target_pct=P("TARGET_PCT"),
               setting_buffer_x=P("BUFFER_LOSS_X"), setting_buffer_delta=P("BUFFER_SHORT_DELTA"), setting_max_hold_days=MAX_HOLD_DAYS, setting_exit_time=EXIT_TIME,
               setting_short_mode=P("SHORT_MODE"), setting_short_delta=P("SHORT_DELTA"), setting_hedge_mode=P("HEDGE_MODE"), setting_hedge_delta=P("HEDGE_DELTA"),
               setting_hedge_points=P("HEDGE_POINTS"), setting_auto_reenter=AUTO)
    append_csv(LEDGER, LEDGER_COLS, row)
    append_csv(EVENTS, EVENT_COLS, dict(time=str(nw), trade_id=tid, event="EXIT", expiry=st["expiry"], short_strike=s["strike"], long_strike=l["strike"],
               short_px=m["xs"], long_px=m["xl"], spot=spot, short_delta=gS["delta"], short_iv=gS["iv"], net_pnl=round(m["net"], 2),
               charges=round(st["exit"]["charges"], 2), reason=reason, qty=q))
    save_state()

def step(nw, wall, qs):
    st = state; spot = spot_of(qs); age = feed_age(qs, wall)
    if st.get("idle"): return
    if st["status"] == "OPEN" and st.get("kind") != "SINGLE":
        st["target"] = P("TARGET_PCT")/100*st["credit"]*st["qty"]; st["buffer"] = P("BUFFER_LOSS_X")*st["credit"]*st["qty"]
    alive = age is not None and age < 15 and market_open(nw)
    if st["status"] == "WAITING":
        st["qty"] = (((FUT_LOTS if st.get("opt") == "FUT" else NK_LOTS) if st.get("kind") == "SINGLE" else P("LOTS")))*LOT
        p = st["plan"]; qS, qL = qs.get(p["short"]["key"]) or {}, qs.get(p["long"]["key"]) or {}
        due = st.get("force") or ENTRY_TIME == "now" or mins(nw) >= hm(ENTRY_TIME)
        if alive and due and hm("09:20") <= mins(nw) < hm(ENTRY_CUTOFF) and qS.get("ltp") and qL.get("ltp"):
            do_entry(nw, qs)
    elif st["status"] == "OPEN" and alive:
        m = mark(qs)
        if not m: return
        st["_exit_ch_est"] = m["exit_ch"]
        st["max_pnl"] = max(st.get("max_pnl", m["net"]), m["net"]); st["min_pnl"] = min(st.get("min_pnl", m["net"]), m["net"])
        if wall-(st["hist"][-1][0] if st["hist"] else 0) >= 5:
            st["hist"].append([wall, m["net"]]); st["hist"] = st["hist"][-3000:]
        gs = leg_greeks(m["qS"], st["short"]["strike"], spot); dte = (exp_date()-nw.date()).days; late = mins(nw) >= hm(EXIT_TIME)
        if PARTIAL_ON and not st.get("partial_done") and st["target"] > 0 and m["net"] >= PARTIAL_AT_PCT/100*st["target"]:
            st["partial_done"] = True; lots_ = st["qty"]//max(st.get("lot", LOT), 1)
            if lots_ >= 2 and partial_exit(nw, qs, st["pid"], max(1, round(lots_*PARTIAL_CLOSE_PCT/100)), f"PARTIAL: {PARTIAL_AT_PCT:.0f}% of target reached", auto=True): return
        if st.get("be_stop") and m["net"] <= 0: do_exit(nw, m, "STOP: back to breakeven after the partial exit")
        elif m["net"] >= st["target"]: do_exit(nw, m, "TARGET hit")
        elif m["net"] <= -st["buffer"]: do_exit(nw, m, "BUFFER: loss limit")
        elif st.get("kind") != "SINGLE" and gs["delta"] is not None and abs(gs["delta"]) >= P("BUFFER_SHORT_DELTA"): do_exit(nw, m, "BUFFER: short delta")
        elif INTRADAY_ONLY and (late or st.get("entry_date") != str(nw.date())): do_exit(nw, m, "INTRADAY: square-off")
        elif late and held_days(nw) >= MAX_HOLD_DAYS: do_exit(nw, m, f"TIME: {MAX_HOLD_DAYS}-day max hold")
        elif late and dte <= MIN_DTE_EXIT: do_exit(nw, m, "TIME: near expiry")

def book():
    if state.get("status") == "CLOSED" and not state.get("_booked"):
        state["_booked"] = True; session["realized"] += state["exit"]["net"]; session["n"] += 1
        ACCOUNT["realized"] += state["exit"]["net"]; save_account()
        save_session(); save_state(); return state["exit"]["reason"]

def to_waiting(side=None):
    side = side or cur_side()
    try: _ex = trade_expiry(); newp = pick_legs(fetch_chain(_ex, side), side); newp["expiry"] = str(_ex)
    except Exception as e: say(f"Could not fetch option chain: {e}"); return False
    old = ([state["short"]["key"], state["long"]["key"]] if "short" in state
           else [state["plan"]["short"]["key"], state["plan"]["long"]["key"]] if "plan" in state else [])
    new = [newp["short"]["key"], newp["long"]["key"]]; lg = state.get("log", [])
    ns = new_state(newp); ns["log"] = lg; state.clear(); state.update(ns)
    change_subs([k for k in old if k not in new], [k for k in new if k not in old]); set_cur_legs()
    log(f"New plan ({'bear call' if side == 'CALL' else 'bull put'}): SELL {newp['short']['strike']:.0f} / BUY {newp['long']['strike']:.0f}")
    return True

# ------------------------------------------------------------------ dashboard snapshot
def status_line(nw, live_mo):
    st = state
    if st["status"] == "WAITING":
        if st.get("idle"): return "FLAT: no trades open. Use the Enter buttons to start one."
        if not market_open(nw): return "WAITING: market closed"
        if not live_mo: return "WAITING: no live feed yet"
        if ENTRY_TIME != "now" and mins(nw) < hm(ENTRY_TIME): return f"WAITING: entry at {ENTRY_TIME}"
        return "WAITING: entering as soon as both legs have live quotes"
    if st["status"] == "OPEN": return f"HOLDING since {st['entry_time'][:16]}: exits on target, buffer, or time rule"
    return f"CLOSED: {st['exit']['reason']} | net {money(st['exit']['net'])}"

def settings_dict():
    out = []
    for k, t in EDITABLE.items():
        v = globals()[k]
        out.append(dict(name=k, value=v, type=("bool" if t is bool else "select" if isinstance(t, tuple) else "text" if isinstance(t, str) else "num"),
                        options=list(t) if isinstance(t, tuple) else None))
    return out

def clean(o):
    if isinstance(o, float): return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [clean(v) for v in o]
    if isinstance(o, (np.floating, np.integer)): return clean(o.item())
    return o

def build_snapshot(nw, wall, qs):
    st = state; spot = spot_of(qs); age = feed_age(qs, wall); mo = market_open(nw); live = bool(age is not None and age < 15)
    q = st.get("qty", LOT*P("LOTS")); status = st["status"]
    src = st if status != "WAITING" else st["plan"]; sL, lL = src["short"], src["long"]
    qS, qL = qs.get(sL["key"]) or {}, qs.get(lL["key"]) or {}
    gS, gL = leg_greeks(qS, sL["strike"], spot), leg_greeks(qL, lL["strike"], spot)
    def row(role, leg, qq, g):
        return dict(role=role, strike=leg["strike"], entry=leg.get("entry_px"), ltp=qq.get("ltp"), bid=qq.get("bid"), ask=qq.get("ask"),
                    delta=g["delta"], theta=g["theta"], vega=g["vega"], iv=g["iv"], src=g["src"])
    snap = dict(time=nw.strftime("%a %d %b %H:%M:%S"), status=status, feed=dict(live=live, market_open=mo), spot=spot, qty=q,
                expiry=exp_date().strftime("%d %b %Y"), dte=(exp_date()-nw.date()).days, exp_list=[str(e) for e in exp_choices()], trade_exp=str(trade_expiry()), status_line=status_line(nw, live and mo),
                opt=OT(), strategy=((("Buy " if st.get("dirn", 1) > 0 else "Sell ")+st.get("ilabel", "")) if st.get("kind") == "SINGLE" else ("Bear call spread" if cur_side() == "CALL" else "Bull put spread")), legs=([row("BUY" if st.get("dirn", 1) > 0 else "SELL", sL, qS, gS)] if st.get("kind") == "SINGLE" else [row("SELL", sL, qS, gS), row("BUY", lL, qL, gL)]), instr=(st.get("ilabel", "") if st.get("kind") == "SINGLE" else ""), mode=TRADE_MODE, backtest=BACKTEST_MODE, auto=AUTO, engine="stopped" if ENGINE_STOP.is_set() else "running",
                width=abs(sL["strike"]-lL["strike"]), hist=[], rules=[], pnl=None, plan=None, nets=None)
    op = 0.0
    if status == "WAITING":
        sp = px_entry_short(qS) if qS.get("ltp") else None; lp = px_entry_long(qL) if qL.get("ltp") else None
        cr = (sp-lp) if sp is not None and lp is not None else None
        snap["plan"] = dict(credit=cr, credit_rs=cr*q if cr is not None else None,
                            max_loss=(snap["width"]-(cr or 0))*q, min_hold="")
    else:
        m = mark(qs) if status == "OPEN" else None
        if status == "CLOSED": net, gross, chg = st["exit"]["net"], st["exit"]["gross"], st["exit"]["charges"]
        elif m: net, gross, chg = m["net"], m["gross"], st["entry_charges"]+m["exit_ch"]
        else: net, gross, chg = 0, 0, st["entry_charges"]
        op = net if status == "OPEN" else 0.0
        snap["pnl"] = dict(net=net, gross=gross, charges=chg, target=st["target"], buffer=st["buffer"], credit=st["credit"],
                           target_pct=(net/st["target"]*100 if st["target"] else 0),
                           room_pct=max(0, st["buffer"]+net)/st["buffer"]*100 if st["buffer"] else 0,
                           breakeven=(st["short"]["entry_px"] if st.get("kind") == "SINGLE" else st["short"]["strike"]+(1 if cur_side() == "CALL" else -1)*st["credit"]), held=held_days(nw) if "entry_date" in st else 0, max_pnl=st.get("max_pnl"), min_pnl=st.get("min_pnl"),
                           max_profit=st["credit"]*q, max_loss=(st["buffer"] if st.get("kind") == "SINGLE" else (st["width"]-st["credit"])*q), entry_spot=st.get("entry_spot"), trade_id=st.get("trade_id", ""), expiry=exp_date().strftime("%a %d %b %Y"), entry_dte=st.get("entry_dte"),
                           entry_time=st.get("entry_time", "")[:16], exit=st.get("exit"))
        if gS["delta"] is not None and gL["delta"] is not None:
            snap["nets"] = dict(delta=q*(-gS["delta"]+gL["delta"]), theta=q*(-(gS["theta"] or 0)+(gL["theta"] or 0)),
                                vega=q*(-(gS["vega"] or 0)+(gL["vega"] or 0)))
        if status == "OPEN" and m:
            d = abs(gS["delta"]) if gS["delta"] is not None else None; dte = snap["dte"]; hd = held_days(nw); net = m["net"]
            snap["rules"] = [dict(name="Take profit", now=money(net), limit=money(st["target"]), trig=net >= st["target"]),
                             dict(name="Loss buffer", now=money(net), limit=money(-st["buffer"]), trig=net <= -st["buffer"]),
                             dict(name="Short-put delta", now=("-" if d is None else f"{d:.3f}"), limit=str(P("BUFFER_SHORT_DELTA")), trig=d is not None and d >= P("BUFFER_SHORT_DELTA")),
                             dict(name="Max hold", now=f"{hd}d", limit=f"{MAX_HOLD_DAYS}d @ {EXIT_TIME}", trig=hd >= MAX_HOLD_DAYS),
                             dict(name="Near expiry", now=f"DTE {dte}", limit=f"<= {MIN_DTE_EXIT}", trig=dte <= MIN_DTE_EXIT)]
        h = st.get("hist", [])[-600:]; seen = set(); hh = []
        for w, v in h:
            t = int(w)+SHIFT
            if t not in seen: seen.add(t); hh.append([t, v])
        snap["hist"] = hh
    tot = session["realized"]+op
    snap["session"] = dict(n=session["n"], realized=session["realized"], open=op, total=tot, max_trades=MAX_TRADES_PER_DAY)
    snap["log"] = GLOG[-14:]; snap["msgs"] = MSGS[-6:]; snap["settings"] = settings_dict()
    poss = []; openpnl = 0.0; marg = 0.0
    for p_ in list(POSITIONS):
        if p_.get("idle"): continue
        with use(p_):
            m_ = mark(qs) if p_["status"] == "OPEN" else None
            net_ = m_["net"] if m_ else ((p_.get("exit") or {}).get("net", 0.0) if p_["status"] == "CLOSED" else 0.0)
            sd_ = cur_side(); sh_, lg_ = (p_ if p_["status"] != "WAITING" else p_["plan"])["short"], (p_ if p_["status"] != "WAITING" else p_["plan"])["long"]
            poss.append(dict(pid=p_.get("pid"), label=((('Buy ' if p_.get('dirn', 1) > 0 else 'Sell ')+p_.get('ilabel', '')) if p_.get('kind') == 'SINGLE' else f"{'Bull put' if sd_ == 'PUT' else 'Bear call'} {sh_['strike']:.0f}/{lg_['strike']:.0f}"), status=p_["status"], net=net_,
                             qty=p_.get("qty"), lots=(p_.get("qty") or 0)//max(p_.get("lot", LOT), 1), entry=(p_.get("entry_time") or "")[11:16], reason=(p_.get("exit") or {}).get("reason", ""), origin=p_.get("origin", "")))
            if p_["status"] == "OPEN": openpnl += net_; marg += (p_.get("width") or 0)*(p_.get("qty") or 0)
    snap["positions"] = poss; snap["sel"] = SEL["pid"]
    snap["session"]["open"] = openpnl; snap["session"]["total"] = session["realized"]+openpnl
    eq_ = ACCOUNT["start"] + ACCOUNT["realized"] + openpnl; ACCOUNT["peak"] = max(ACCOUNT.get("peak", eq_), eq_); ACCOUNT["maxdd"] = max(ACCOUNT.get("maxdd", 0.0), ACCOUNT["peak"]-eq_)
    snap["account"] = dict(start=ACCOUNT["start"], realized=ACCOUNT["realized"], open=openpnl, equity=eq_, margin=marg, available=eq_-marg,
                           ret_pct=(eq_/ACCOUNT["start"]-1)*100 if ACCOUNT["start"] else 0, peak=ACCOUNT["peak"], maxdd=ACCOUNT["maxdd"], created=str(ACCOUNT.get("created", ""))[:16], resets=ACCOUNT.get("resets", 0))
    if st.get("kind") == "SINGLE": snap["rules"] = [r for r in snap["rules"] if r["name"] not in ("Short-put delta", "Near expiry", "Max hold")]
    snap["signals"] = SIGDATA
    snap["triggers"] = [dict(t, other=(t.get("pid") is not None and t.get("pid") != SEL["pid"]), dormant=(t["sig"] != series_info(t["series"])[0] or t["status"] == "expired" or (t.get("pid") is not None and t.get("pid") != SEL["pid"]))) for t in TRIGGERS]
    return clean(snap)

# ------------------------------------------------------------------ engine thread

# ------------------------------------------------------------------ SIGNALS (futures VWAP, relative volume, opening range, structure, EMA 9/21)
SIGDATA = dict(items=[], verdict="NEUTRAL", detail="waiting for data…", log=[], updated="")
SIG_RAW = {}
def _sig_eval(key, fns, wall):
    try: st, det = _sr_call(fns[key], key)       # same call; the research logger also keeps the values it computed
    except Exception as e: st, det = "NA", f"error: {e}"
    SIG_RAW[key] = (st, det, wall); return SIG_RAW[key]
def _vwap_tick(t):
    try: st, det = _sr_call(sig_vwap, "vwap"); SIG_RAW["vwap"] = (st, det, t)
    except Exception: pass
SIG_PREV = {}; SIG_LOG = []; SIG_LAST_ENTRY = 0.0; SIG_STABLE = (None, 0.0)
def fetch_oi(expiry):
    r = requests.get("https://api.upstox.com/v2/option/chain", headers=HDR, params={"instrument_key": UNDERLYING_KEY, "expiry_date": str(expiry)}, timeout=20); r.raise_for_status(); rows = []
    for x in r.json()["data"]:
        c = (x.get("call_options") or {}).get("market_data") or {}; p_ = (x.get("put_options") or {}).get("market_data") or {}
        rows.append((float(x["strike_price"]), float(c.get("oi") or 0), float(c.get("prev_oi") or 0), float(p_.get("oi") or 0), float(p_.get("prev_oi") or 0)))
    return rows

def sig_oi():
    rows = OI_CACHE.get("rows"); sp = spot_now(); bars = _bars5(UNDERLYING_KEY)
    if not rows or not sp: return "NA", "waiting for option open-interest data"
    if not bars: return "NA", "waiting for the first completed 5-min candle"
    win = [r for r in rows if abs(r[0]-sp) <= sp*0.03]
    if not win or max(r[1] for r in win) <= 0: return "NA", "no open-interest numbers returned for this expiry"
    cw, pw = max(win, key=lambda r: r[1]), max(win, key=lambda r: r[3]); cl = bars[-1]["close"]
    st = "BULL" if cl > cw[0]+SIG_OI_BUFFER else "BEAR" if cl < pw[0]-SIG_OI_BUFFER else "NEUTRAL"
    where = "ABOVE the call-writer wall: resistance is breaking" if st == "BULL" else "BELOW the put-writer wall: support is breaking" if st == "BEAR" else "between the two walls"
    return st, (f"Call wall (resistance) {cw[0]:.0f}: {cw[1]/1e5:.1f}L open interest ({(cw[1]-cw[2])/1e5:+.1f}L today). Put wall (support) {pw[0]:.0f}: {pw[3]/1e5:.1f}L ({(pw[3]-pw[4])/1e5:+.1f}L today). "
                f"Last 5-min close {cl:,.1f} is {where}")

def sig_contrib():
    if not EQ_KEYS: return "NA", "heavyweight stocks not found in the instrument file"
    with lock: iq = dict(quotes.get(UNDERLYING_KEY) or {}); eq = {k: dict(quotes.get(v) or {}) for k, v in EQ_KEYS.items()}
    icp = iq.get("cp"); rows = []
    if not icp or not iq.get("ltp"): return "NA", "waiting for the index's previous close"
    for sym, q in eq.items():
        if q.get("ltp") and q.get("cp"): rows.append((sym, NIFTY_WEIGHTS[sym]/100*(q["ltp"]-q["cp"])/q["cp"]*icp, NIFTY_WEIGHTS[sym]))
    if len(rows) < 6: return "NA", f"only {len(rows)} of {len(EQ_KEYS)} heavyweight stocks have live prices"
    tot = sum(r[1] for r in rows); up_w = sum(r[2] for r in rows if r[1] > 0); dn_w = sum(r[2] for r in rows if r[1] < 0); share = up_w/max(up_w+dn_w, 1e-9)
    st = "BULL" if tot >= SIG_CONTRIB_MIN and share >= 0.6 else "BEAR" if tot <= -SIG_CONTRIB_MIN and (1-share) >= 0.6 else "NEUTRAL"
    srt = sorted(rows, key=lambda r: r[1]); idx_move = iq["ltp"]-icp
    f = lambda L: ", ".join(f"{a} {b:+.0f}" for a, b, _ in L)
    return st, f"These {len(rows)} heavyweights add {tot:+.0f} pts of the index's {idx_move:+.0f} pts. Pushing up: {f(srt[::-1][:3])}. Dragging down: {f(srt[:3])}"

SIGDEF = [("vwap", "1. Futures VWAP + slope + distance", "SIG_USE_VWAP", "Bullish: futures above a rising VWAP and the gap to VWAP is widening or holding (not shrinking). Bearish: below a falling VWAP with the gap not shrinking. Beyond 1 sigma with VWAP still sloping the same way is marked STRONG."),
          ("rvol", "2. Futures relative volume", "SIG_USE_RVOL", "Latest 5-min futures candle volume vs usual for that time of day, with price breaking the previous candle in that direction."),
          ("orb", "3. Opening-range breakout", "SIG_USE_ORB", "NIFTY spot 5-min candle CLOSE above the first-15-minute high (bullish) or below its low (bearish)."),
          ("struct", "4. Price structure", "SIG_USE_STRUCT", "Bullish: higher highs and higher lows. Bearish: lower highs and lower lows (5-min swing points)."),
          ("ema", "5. EMA 9 / EMA 21", "SIG_USE_EMA", "Bullish: EMA9 above EMA21, both rising. Bearish: EMA9 below EMA21, both falling (5-min)."),
          ("oi", "6. OI wall break", "SIG_USE_OI", "Where option writers have piled in. Bullish: price closes above the biggest call-OI strike (resistance gives way). Bearish: closes below the biggest put-OI strike (support gives way)."),
          ("contrib", "7. Index point contribution", "SIG_USE_CONTRIB", "How many Nifty points the biggest stocks are adding or removing today. Bullish: they add a lot and most of their weight is up. Bearish: the opposite."),
          ("delta", "8. Delta / footprint divergence", "SIG_USE_DELTA", "Bullish: price makes a lower low but delta makes a higher low (sellers fading). Bearish: price makes a higher high but delta makes a lower high (buyers fading). Delta is estimated from tick direction vs bid/ask on 1-min futures. Plays out over about 1-5 minutes."),
          ("sweep", "9. Liquidity sweep + rejection", "SIG_USE_SWEEP", "Bullish: a 1-min candle sweeps below the recent low and closes strongly back up. Bearish: sweeps above the recent high and closes strongly back down. Acts immediately."),
          ("vwband", "10. VWAP bands (1-2 sigma) + slope", "SIG_USE_VWBAND", "Bullish: price flushed out to about -2 sigma and reclaims -1 sigma while VWAP is flattening or turning up. Bearish: price spiked out to about +2 sigma and loses +1 sigma while VWAP is flattening or turning down. Plays out over about 2-8 minutes."),
          ("orbv", "11. Opening range + volume (bias holds)", "SIG_USE_ORBV", "Bullish: a 5-min spot candle closes above the first-15-minute high on at least the 'Volume must be at least this many times the usual' multiple of normal futures volume for that time of day; the bias then holds until a 5-min close below the range low. Bearish is the mirror."),
          ("dtrend", "12. Cumulative delta trend", "SIG_USE_DTREND", "Bullish: 5-min futures making higher highs and higher lows while net delta (estimated buying minus selling) is positive. Bearish: lower highs and lows with negative delta. If price and delta disagree it shows a divergence warning instead.")]
PRED = {"BULL": "Predicts UPSIDE: favours selling a BULL PUT spread", "BEAR": "Predicts DOWNSIDE: favours selling a BEAR CALL spread",
        "NEUTRAL": "No clear direction: no edge", "NA": "Not enough data yet"}
LBL = {"BULL": "BULLISH", "BEAR": "BEARISH", "NEUTRAL": "NEUTRAL", "NA": "NO DATA"}

def _today(): return (int(time.time())+SHIFT)//86400
def _bars5(key):
    if not key: return []
    with clock: d = dict(CANDLES.get(key, {}))
    cut = int(time.time())+SHIFT
    return [b for b in aggregate(d, 5) if b["time"]+300 <= cut]          # completed 5-minute candles only
def _ema(vals, p):
    if len(vals) < p: return []
    k = 2/(p+1); e = sum(vals[:p])/p; out = [e]
    for v in vals[p:]: e = v*k+e*(1-k); out.append(e)
    return out

def sig_vwap():
    if not FUT_KEY: return "NA", "no futures contract found"
    with clock: d = dict(CANDLES.get(FUT_KEY, {}))
    ms = sorted(m for m in d if (m+SHIFT)//86400 == _today())
    if not ms: return "NA", "no futures candles yet today"
    pv = pv2 = vv = 0.0; ser = []
    for m in ms:
        c = d[m]; v = c[4] if len(c) > 4 else 0.0; tp = (c[1]+c[2]+c[3])/3; pv += tp*v; pv2 += tp*tp*v; vv += v
        if vv > 0: w = pv/vv; ser.append((m, w, math.sqrt(max(pv2/vv-w*w, 0.0)), c[3]))        # time, vwap, sigma, close
    if not ser: return "NA", "no futures volume yet (VWAP needs volume)"
    last, vw, sd = d[ms[-1]][3], ser[-1][1], ser[-1][2]
    then = [x for x in ser if x[0] <= ser[-1][0]-SIG_SLOPE_MIN*60]
    if not then: return "NA", "building the VWAP slope…"
    t = then[-1]; sl = vw-t[1]; gap, gap0 = last-vw, t[3]-t[1]
    up, dn = last > vw+SIG_VWAP_BUFFER and sl > 0, last < vw-SIG_VWAP_BUFFER and sl < 0      # bullish / bearish start a little further from VWAP
    shrink = (up and gap0 > 0 and gap < gap0*SIG_VWAP_COMPRESS) or (dn and gap0 < 0 and -gap < -gap0*SIG_VWAP_COMPRESS)
    st = "BULL" if up and not shrink else "BEAR" if dn and not shrink else "NEUTRAL"
    z = gap/sd if sd >= SIG_VWB_MIN_SIGMA else None
    det = (f"Futures {last:,.1f} vs VWAP {vw:,.1f}; VWAP {'rising' if sl > 0 else 'falling'} {sl:+.1f} pts over {SIG_SLOPE_MIN} min; "
           f"gap {gap0:+.1f} -> {gap:+.1f} pts ({'shrinking' if shrink else 'widening' if abs(gap) > abs(gap0) else 'holding'})")
    if shrink: det += ": price is drifting back toward VWAP, so no clean bias"
    if z is not None:
        det += f"; {z:+.1f} sigma from VWAP"
        if (st == "BULL" and z >= 1) or (st == "BEAR" and z <= -1): det += " -> STRONG: beyond 1 sigma with VWAP still sloping the same way, continuation more likely"
    return st, det

def sig_rvol():
    bars = _bars5(FUT_KEY); tb = [b for b in bars if b["time"]//86400 == _today()]
    if len(tb) < 2: return "NA", "need 2 completed futures 5-min candles"
    last, prev = tb[-1], tb[-2]; tod = last["time"] % 86400; src = "same time on previous days"
    past = [b["volume"] for b in bars if b["time"]//86400 < _today() and b["time"] % 86400 == tod and b["volume"] > 0]
    base = sum(past)/len(past) if past else None
    if base is None and len(tb) >= 4:
        e = [b["volume"] for b in tb[:-1] if b["volume"] > 0]; base = sum(e)/len(e) if e else None; src = "earlier candles today"
    if not base or last["volume"] <= 0: return "NA", "no futures volume history yet"
    r = last["volume"]/base; green = last["close"] > last["open"]
    up = green and last["high"] > prev["high"]; dn = (not green) and last["close"] < last["open"] and last["low"] < prev["low"]
    st = "BULL" if r >= SIG_RVOL_MIN and up else "BEAR" if r >= SIG_RVOL_MIN and dn else "NEUTRAL"
    return st, f"Last 5-min volume {r:.1f}x usual ({src}); {'green' if green else 'red'} candle; needs {SIG_RVOL_MIN}x and a break of the previous candle's {'high' if green else 'low'}"

def sig_orb():
    with clock: d = dict(CANDLES.get(UNDERLYING_KEY, {}))
    t0 = (int(time.time())+SHIFT) % 86400
    rng = [d[m] for m in d if (m+SHIFT)//86400 == _today() and 33300 <= (m+SHIFT) % 86400 < 34200]
    if not rng: return "NA", "no spot candles for 09:15-09:30 today"
    if t0 < 34200: return "NA", "opening range still forming (until 09:30)"
    hi, lo = max(c[1] for c in rng), min(c[2] for c in rng)
    bars = [b for b in _bars5(UNDERLYING_KEY) if b["time"]//86400 == _today() and b["time"] % 86400 >= 34200]
    if not bars: return "NEUTRAL", f"range {lo:,.1f} - {hi:,.1f}; waiting for the first 5-min close after 09:30"
    cl = bars[-1]["close"]; st = "BULL" if cl > hi else "BEAR" if cl < lo else "NEUTRAL"
    return st, f"Opening range {lo:,.1f} - {hi:,.1f}; last 5-min close {cl:,.1f} ({'above' if cl > hi else 'below' if cl < lo else 'inside'})"

def sig_struct():
    bars = [b for b in _bars5(UNDERLYING_KEY) if b["time"]//86400 == _today()]
    if len(bars) < 7: return "NA", f"need 7 completed 5-min candles, have {len(bars)}"
    ph = [bars[i]["high"] for i in range(2, len(bars)-2) if bars[i]["high"] > max(bars[j]["high"] for j in (i-2, i-1, i+1, i+2))]
    pl = [bars[i]["low"] for i in range(2, len(bars)-2) if bars[i]["low"] < min(bars[j]["low"] for j in (i-2, i-1, i+1, i+2))]
    if len(ph) < 2 or len(pl) < 2: return "NEUTRAL", "not enough swing highs/lows yet"
    hh, hl, lh, ll = ph[-1] > ph[-2], pl[-1] > pl[-2], ph[-1] < ph[-2], pl[-1] < pl[-2]
    st = "BULL" if hh and hl else "BEAR" if lh and ll else "NEUTRAL"
    return st, f"Swing highs {ph[-2]:,.0f} -> {ph[-1]:,.0f} ({'higher' if hh else 'lower' if lh else 'equal'}); swing lows {pl[-2]:,.0f} -> {pl[-1]:,.0f} ({'higher' if hl else 'lower' if ll else 'equal'})"

def sig_ema():
    cl = [b["close"] for b in _bars5(UNDERLYING_KEY)]; e9, e21 = _ema(cl, 9), _ema(cl, 21)
    if len(e21) < 2: return "NA", f"need 22+ completed 5-min candles, have {len(cl)}"
    a, b, c, d = e9[-1], e9[-2], e21[-1], e21[-2]
    st = "BULL" if a > c and a > b and c > d else "BEAR" if a < c and a < b and c < d else "NEUTRAL"
    return st, f"EMA9 {a:,.1f} ({'rising' if a > b else 'falling'}) vs EMA21 {c:,.1f} ({'rising' if c > d else 'falling'})"


def sig_delta():
    if not FUT_KEY: return "NA", "no futures contract found"
    with clock: d = dict(CANDLES.get(FUT_KEY, {})); dl = dict(DELTA.get(FUT_KEY, {}))
    if not dl: return "NA", "no live futures volume yet. Delta is built from live ticks, so it starts empty every time you start the script"
    first = min(dl); now_m = int(time.time()//60*60)
    ms = sorted(m for m in d if first <= m < now_m and (m+SHIFT)//86400 == _today())
    if len(ms) < SIG_DELTA_MIN_BARS: return "NA", f"building delta history: {len(ms)} of {SIG_DELTA_MIN_BARS} completed minutes"
    cv = []; run = 0.0
    for m in ms: run += dl.get(m, 0.0); cv.append(run)
    ms, cv = ms[-SIG_DELTA_LOOKBACK:], cv[-SIG_DELTA_LOOKBACK:]
    lo, hi = [d[m][2] for m in ms], [d[m][1] for m in ms]; n = len(ms); k = 2
    pl = [i for i in range(k, n-k) if lo[i] < min(lo[j] for j in range(i-k, i+k+1) if j != i)]
    ph = [i for i in range(k, n-k) if hi[i] > max(hi[j] for j in range(i-k, i+k+1) if j != i)]
    bull = len(pl) >= 2 and lo[pl[-1]] < lo[pl[-2]] and cv[pl[-1]] > cv[pl[-2]] and n-1-pl[-1] <= SIG_DELTA_FRESH
    bear = len(ph) >= 2 and hi[ph[-1]] > hi[ph[-2]] and cv[ph[-1]] < cv[ph[-2]] and n-1-ph[-1] <= SIG_DELTA_FRESH
    if bull and not bear:
        a, b = pl[-2], pl[-1]
        return "BULL", f"Price made a LOWER low ({lo[a]:,.1f} -> {lo[b]:,.1f}) but delta made a HIGHER low ({cv[a]:+,.0f} -> {cv[b]:+,.0f}): sellers are running out of steam (estimated delta, 1-min futures)"
    if bear and not bull:
        a, b = ph[-2], ph[-1]
        return "BEAR", f"Price made a HIGHER high ({hi[a]:,.1f} -> {hi[b]:,.1f}) but delta made a LOWER high ({cv[a]:+,.0f} -> {cv[b]:+,.0f}): buyers are running out of steam (estimated delta, 1-min futures)"
    t = lambda arr, idx, nm: (f"{nm} {arr[idx[-2]]:,.1f} -> {arr[idx[-1]]:,.1f}" if len(idx) >= 2 else f"{nm}: fewer than 2 swings yet")
    return "NEUTRAL", f"No divergence. {t(lo, pl, 'swing lows')}; {t(hi, ph, 'swing highs')}; cumulative delta now {cv[-1]:+,.0f}"

def sig_sweep():
    with clock: d = dict(CANDLES.get(UNDERLYING_KEY, {}))
    if not d: return "NA", "no spot candles yet"
    cut = int(time.time())+SHIFT; L = SIG_SWEEP_LOOKBACK
    bars = [b for b in aggregate(d, 1) if b["time"]+60 <= cut and b["time"]//86400 == _today()]
    if len(bars) < L+2: return "NA", f"need {L+2} completed 1-min spot candles today, have {len(bars)}"
    px = d[max(d)][3]; c = bars[-1]; rl = rh = None
    for back in (1, 2):                                   # the latest completed candle, or the one just before it
        c = bars[-back]; ref = bars[-back-L:-back]; rl, rh = min(b["low"] for b in ref), max(b["high"] for b in ref)
        rng = c["high"]-c["low"]
        if rng < SIG_SWEEP_MIN_PTS: continue
        up = (c["close"]-c["low"])/rng; when = "The latest" if back == 1 else "The previous"
        if c["low"] < rl-SIG_SWEEP_MIN_DEPTH and c["close"] > rl and up >= SIG_SWEEP_CLOSE_FRAC and px > rl:
            return "BULL", f"{when} 1-min candle swept the {L}-min low {rl:,.1f} (wick to {c['low']:,.1f}) and closed back at {c['close']:,.1f}, {up*100:.0f}% up its range. Stops below the low were taken, price rejected"
        if c["high"] > rh+SIG_SWEEP_MIN_DEPTH and c["close"] < rh and (1-up) >= SIG_SWEEP_CLOSE_FRAC and px < rh:
            return "BEAR", f"{when} 1-min candle swept the {L}-min high {rh:,.1f} (wick to {c['high']:,.1f}) and closed back at {c['close']:,.1f}, {(1-up)*100:.0f}% down its range. Stops above the high were taken, price rejected"
    b = bars[-1]
    return "NEUTRAL", f"No sweep. Last {L}-min range {min(x['low'] for x in bars[-L-1:-1]):,.1f} - {max(x['high'] for x in bars[-L-1:-1]):,.1f}; last 1-min candle {b['low']:,.1f} - {b['high']:,.1f}, close {b['close']:,.1f}"

def sig_vwband():
    if not FUT_KEY: return "NA", "no futures contract found"
    with clock: d = dict(CANDLES.get(FUT_KEY, {}))
    ms = sorted(m for m in d if (m+SHIFT)//86400 == _today() and len(d[m]) > 4 and d[m][4] > 0)
    if len(ms) < SIG_VWB_MIN_BARS: return "NA", f"need {SIG_VWB_MIN_BARS} futures minutes with volume, have {len(ms)}"
    vv = pv = pv2 = 0.0; ser = []
    for m in ms:
        c = d[m]; v = c[4]; tp = (c[1]+c[2]+c[3])/3; vv += v; pv += tp*v; pv2 += tp*tp*v
        w = pv/vv; ser.append((w, math.sqrt(max(pv2/vv-w*w, 0.0)), c[1], c[2], c[3]))      # vwap, sigma, high, low, close
    w, sd, _, _, cl = ser[-1]; n = SIG_VWB_SLOPE_MIN
    if sd < SIG_VWB_MIN_SIGMA: return "NA", f"VWAP bands too narrow to trade (sigma {sd:.1f} pts, needs {SIG_VWB_MIN_SIGMA:g}): too early or too quiet"
    if len(ser) < 2*n+1: return "NA", "building the VWAP slope…"
    s_now, s_prev = w-ser[-1-n][0], ser[-1-n][0]-ser[-1-2*n][0]
    up_turn = s_now >= -SIG_VWB_FLAT or s_now-s_prev >= SIG_VWB_FLAT       # VWAP flat/rising, or clearly stopped falling
    dn_turn = s_now <= SIG_VWB_FLAT or s_prev-s_now >= SIG_VWB_FLAT        # VWAP flat/falling, or clearly stopped rising
    win = ser[-SIG_VWB_WINDOW:]
    zl = min((x[4]-x[0])/x[1] for x in win); zh = max((x[4]-x[0])/x[1] for x in win)      # deepest / highest 1-min CLOSE in sigmas over the last few minutes (closes, not wicks)
    dipped, spiked = zl <= -SIG_VWB_EXT, zh >= SIG_VWB_EXT                 # needs a real flush/spike out to the outer band first
    if dipped and spiked:                                                   # both happened: only the clearly bigger move counts
        if -zl > zh+0.5: spiked = False
        elif zh > -zl+0.5: dipped = False
        else: dipped = spiked = False
    reclaim, lose = dipped and cl > w-sd, spiked and cl < w+sd
    sl = f"VWAP {s_now:+.1f} pts over {n} min ({'turning up' if up_turn and s_now > SIG_VWB_FLAT else 'flat' if abs(s_now) <= SIG_VWB_FLAT else 'rising' if s_now > 0 else 'falling'})"
    bands = f"VWAP {w:,.1f}, sigma {sd:.1f} (-2s {w-2*sd:,.1f}, -1s {w-sd:,.1f}, +1s {w+sd:,.1f}, +2s {w+2*sd:,.1f})"
    if reclaim and up_turn and not (lose and dn_turn):
        return "BULL", f"Futures {cl:,.1f} flushed to {-zl:.1f} sigma below VWAP and has reclaimed -1s ({w-sd:,.1f}); {sl}. {bands}"
    if lose and dn_turn and not (reclaim and up_turn):
        return "BEAR", f"Futures {cl:,.1f} spiked to {zh:.1f} sigma above VWAP and has lost +1s ({w+sd:,.1f}); {sl}. {bands}"
    return "NEUTRAL", f"Futures {cl:,.1f} is {(cl-w)/sd:+.1f} sigma from VWAP; {sl}. {bands}"


def _hhmm(t): return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M")          # candle times are already IST-shifted

def _rvol_at(fbars, bt):
    """Futures volume of the 5-min candle starting at bt, as a multiple of the usual for that time of day."""
    cur = next((b for b in fbars if b["time"] == bt), None)
    if not cur or cur["volume"] <= 0: return None, "no futures volume for that candle"
    past = [b["volume"] for b in fbars if b["time"]//86400 < _today() and b["time"] % 86400 == bt % 86400 and b["volume"] > 0]
    if past: return cur["volume"]/(sum(past)/len(past)), "same time on previous days"
    e = [b["volume"] for b in fbars if b["time"]//86400 == _today() and b["time"] < bt and b["volume"] > 0]
    if len(e) >= 3: return cur["volume"]/(sum(e)/len(e)), "earlier candles today"
    return None, "no volume baseline yet"

def sig_orbv():
    with clock: d = dict(CANDLES.get(UNDERLYING_KEY, {}))
    rng = [d[m] for m in d if (m+SHIFT)//86400 == _today() and 33300 <= (m+SHIFT) % 86400 < 34200]
    if not rng: return "NA", "no spot candles for 09:15-09:30 today"
    if (int(time.time())+SHIFT) % 86400 < 34200: return "NA", "opening range still forming (until 09:30)"
    hi, lo = max(c[1] for c in rng), min(c[2] for c in rng)
    bars = [b for b in _bars5(UNDERLYING_KEY) if b["time"]//86400 == _today() and b["time"] % 86400 >= 34200]
    if not bars: return "NEUTRAL", f"range {lo:,.1f} - {hi:,.1f}; waiting for the first 5-min close after 09:30"
    fb = _bars5(FUT_KEY) if FUT_KEY else []
    bias = None; info = None; weak = None; ended = None
    for b in bars:
        cl, hh = b["close"], _hhmm(b["time"])
        if bias == "BULL" and cl < lo: bias = None; ended = f"bullish bias ended at {hh}: a 5-min close fell below the range low"
        elif bias == "BEAR" and cl > hi: bias = None; ended = f"bearish bias ended at {hh}: a 5-min close rose above the range high"
        if (cl > hi or cl < lo) and bias != ("BULL" if cl > hi else "BEAR"):
            side = "BULL" if cl > hi else "BEAR"; r, src = _rvol_at(fb, b["time"])
            if r is not None and r >= SIG_RVOL_MIN: bias = side; info = (hh, cl, r, src); weak = ended = None
            else: weak = (hh, side, r, src)
    now_px = bars[-1]["close"]
    if bias:
        hh, cl, r, src = info; hi_side = bias == "BULL"
        return bias, (f"Range {lo:,.1f} - {hi:,.1f}. The {hh} candle closed {cl:,.1f} {'above the high' if hi_side else 'below the low'} on {r:.1f}x usual volume ({src}). "
                      f"Bias holds until a 5-min close {'below ' + format(lo, ',.1f') if hi_side else 'above ' + format(hi, ',.1f')}; price now {now_px:,.1f}")
    if weak and (weak[0] == _hhmm(bars[-1]["time"]) or now_px > hi or now_px < lo):
        hh, side, r, src = weak
        return "NEUTRAL", f"Range {lo:,.1f} - {hi:,.1f}. The {hh} candle closed {'above' if side == 'BULL' else 'below'} it but " + (f"volume was only {r:.1f}x usual (needs {SIG_RVOL_MIN}x): not confirmed" if r is not None else f"volume can't be checked ({src})")
    return "NEUTRAL", (ended + ". " if ended else "") + f"Price {now_px:,.1f} inside the range {lo:,.1f} - {hi:,.1f}: no confirmed breakout"

def sig_dtrend():
    if not FUT_KEY: return "NA", "no futures contract found"
    with clock: d = dict(CANDLES.get(FUT_KEY, {})); dl = dict(DELTA.get(FUT_KEY, {}))
    if not dl: return "NA", "no live futures volume yet. Delta is built from live ticks, so it starts empty every time you start the script"
    n = SIG_DT_BARS; cut = int(time.time())+SHIFT; first_b = bucket(min(dl), 5)
    bars = [b for b in aggregate(d, 5) if b["time"]+300 <= cut and b["time"]//86400 == _today() and b["time"] > first_b]    # full candles with delta data only
    if len(bars) < n: return "NA", f"building 5-min delta: {len(bars)} of {n} full candles (live data only)"
    w = bars[-n:]; bd = {}
    for m, v in dl.items(): bd[bucket(m, 5)] = bd.get(bucket(m, 5), 0.0)+v
    tot = sum(bd.get(b["time"], 0.0) for b in w); vol = sum(b["volume"] for b in w)
    if vol <= 0: return "NA", "no futures volume in the last candles"
    ratio = tot/vol; h = n//2; ho, hn = max(b["high"] for b in w[:h]), max(b["high"] for b in w[h:]); lo_, ln = min(b["low"] for b in w[:h]), min(b["low"] for b in w[h:])
    p_up, p_dn = hn > ho and ln > lo_, hn < ho and ln < lo_
    dd = f"net delta {tot:+,.0f} on {vol:,.0f} volume ({ratio*100:+.1f}%, needs {SIG_DT_MIN_RATIO*100:.0f}%) over the last {n*5} min (estimated)"
    if p_up and ratio >= SIG_DT_MIN_RATIO: return "BULL", f"Higher highs and higher lows on 5-min futures with buyers in control: {dd}"
    if p_dn and ratio <= -SIG_DT_MIN_RATIO: return "BEAR", f"Lower highs and lower lows on 5-min futures with sellers in control: {dd}"
    if p_up and ratio <= -SIG_DT_MIN_RATIO: return "NEUTRAL", f"WARNING bearish divergence: price still making higher highs and lows but delta disagrees: {dd}"
    if p_dn and ratio >= SIG_DT_MIN_RATIO: return "NEUTRAL", f"WARNING bullish divergence: price still making lower highs and lows but delta disagrees: {dd}"
    return "NEUTRAL", f"{'Higher highs/lows' if p_up else 'Lower highs/lows' if p_dn else 'No clean price trend'}, delta not strong enough: {dd}"


# ------------------------------------------------------------------ SUPPORT / RESISTANCE (where price has turned back on 5m, 15m, 1h)
SR_TF = (("5 min", 5, 3, 1), ("15 min", 15, 2, 2), ("1 hour", 60, 2, 3))      # label, minutes, bars each side of a swing, weight
SR_CACHE = {}; SR_LAST = 0.0; SR_ALERTED = {}

def compute_levels(spot):
    pts = []; cut = int(time.time())+SHIFT
    with clock: d = dict(CANDLES.get(UNDERLYING_KEY, {}))
    for label, tf, n, w in SR_TF:
        bars = [b for b in aggregate(d, tf) if b["time"]+tf*60 <= cut]
        for i in range(n, len(bars)-n):
            near = [bars[j] for j in range(i-n, i+n+1) if j != i]
            if bars[i]["high"] > max(b["high"] for b in near): pts.append((bars[i]["high"], label, w, bars[i]["time"]))
            if bars[i]["low"] < min(b["low"] for b in near): pts.append((bars[i]["low"], label, w, bars[i]["time"]))
    days = sorted({(m+SHIFT)//86400 for m in d if (m+SHIFT)//86400 < _today()})
    if days:
        pdc = [d[m] for m in d if (m+SHIFT)//86400 == days[-1]]
        pts += [(max(c[1] for c in pdc), "prev day high", 2, days[-1]*86400+55000), (min(c[2] for c in pdc), "prev day low", 2, days[-1]*86400+55000)]
    pts.sort(); tol = spot*SR_TOL_PCT/100; groups = []
    for x in pts:
        if groups and x[0]-sum(y[0] for y in groups[-1])/len(groups[-1]) <= tol: groups[-1].append(x)
        else: groups.append([x])
    out = []
    for g in groups:
        tfs = {}                                           # one entry per timeframe, so one swing seen on 5m+15m+1h is not counted 3 times
        for x in g: tfs[x[1]] = w_ = x[2]
        events, last_t = 0, -1e18
        for x in sorted(g, key=lambda y: y[3]):
            if x[3]-last_t >= 3600: events += 1; last_t = x[3]
        strength = sum(tfs.values())
        if strength >= 2:
            out.append(dict(price=sum(x[0] for x in g)/len(g), touches=events, tfs=[t for t in ("5 min", "15 min", "1 hour", "prev day high", "prev day low") if t in tfs],
                            strength=strength, last=datetime.fromtimestamp(max(x[3] for x in g), timezone.utc).strftime("%d %b %H:%M")))
    res = sorted([x for x in out if x["price"] > spot], key=lambda x: x["price"]-spot)[:4]
    sup = sorted([x for x in out if x["price"] <= spot], key=lambda x: spot-x["price"])[:4]
    return dict(spot=spot, resist=res, support=sup, near=max(10.0, spot*0.0005))

def sr_chart_levels():
    L = []
    if SHOW_SR_LINES:
        for x in (SR_CACHE.get("resist") or [])[:1]: L.append(dict(price=x["price"], title="Resistance", color="#e0a04a"))
        for x in (SR_CACHE.get("support") or [])[:1]: L.append(dict(price=x["price"], title="Support", color="#4aa3ff"))
    if SHOW_DMA_LINES:
        sig_dma()                                                  # also starts the daily-history download if it is not loaded yet
        if DMA_CACHE.get("day") == str(date.today()):
            cl = [c for d_, c in DMA_CACHE["rows"] if d_ < str(date.today())]
            for n_, col in ((DMA_FAST, "#f5c542"), (DMA_SLOW, "#4aa3ff"), (DMA_LONG, "#c084fc")):
                if len(cl) >= n_: L.append(dict(price=sum(cl[-n_:])/n_, title=f"{n_} DMA", color=col))
    return L

DMA_CACHE = dict(day=None, rows=[], busy=False, fail=0.0)
def _dma_fetch():
    try:
        to = date.today(); k = urlq(UNDERLYING_KEY, safe="")
        r = requests.get(f"https://api.upstox.com/v3/historical-candle/{k}/days/1/{to}/{to-timedelta(days=430)}", headers=HDR, timeout=30)
        DMA_CACHE.update(day=str(to), rows=sorted((x[0][:10], float(x[4])) for x in r.json()["data"]["candles"]))
    except Exception as e: print("daily history for DMA failed:", e); DMA_CACHE["fail"] = time.time()
    DMA_CACHE["busy"] = False

def sig_dma():
    today = str(date.today())
    if DMA_CACHE["day"] != today:
        if not DMA_CACHE["busy"] and time.time()-DMA_CACHE["fail"] > 120: DMA_CACHE["busy"] = True; threading.Thread(target=_dma_fetch, daemon=True).start()
        return "NA", "loading daily price history for the moving averages…"
    cl = [c for d_, c in DMA_CACHE["rows"] if d_ < today]
    if len(cl) < DMA_SLOW+6: return "NA", f"only {len(cl)} daily candles available, need {DMA_SLOW+6}"
    sma = lambda n, off=0: sum(cl[len(cl)-n-off:len(cl)-off])/n
    fast, slow, f5 = sma(DMA_FAST), sma(DMA_SLOW), sma(DMA_FAST, 5); lng = sma(DMA_LONG) if len(cl) >= DMA_LONG else None
    bars = _bars5(UNDERLYING_KEY); px = bars[-1]["close"] if bars else spot_now()
    if not px: return "NA", "waiting for a Nifty price"
    bull = px > fast > slow and fast > f5 and (lng is None or px > lng); bear = px < fast < slow and fast < f5 and (lng is None or px < lng)
    return ("BULL" if bull else "BEAR" if bear else "NEUTRAL"), (f"Nifty {px:,.0f}: {DMA_FAST} DMA {fast:,.0f} ({'rising' if fast > f5 else 'falling'}), {DMA_SLOW} DMA {slow:,.0f}"
            + (f", {DMA_LONG} DMA {lng:,.0f}" if lng else "") + f". Price is {'above' if px > fast else 'below'} the {DMA_FAST} DMA.")

SIGDEF.append(("dma", "13. DMA trend (daily moving averages)", "SIG_USE_DMA", "The bigger-picture trend. Bullish: price above a rising 20 DMA, which is above the 50 DMA (and above the 200 DMA). Bearish: the opposite."))

VWX = dict(day=None, start=None, prev=None, last_on={}, ev=None, ign=None)
def _vwx_track(raw):
    """Called every engine cycle (~1s) with signal 1's latest state, so no flip is missed between signal-14 checks."""
    now = time.time(); day = _today()
    if VWX["day"] != day: VWX.update(day=day, start=now, prev=None, last_on={}, ev=None, ign=None)
    if not raw: return
    st, det1, t = raw
    if st in ("BULL", "BEAR") and st != VWX["prev"]:
        lo = VWX["last_on"].get(st); watched = t-VWX["start"]
        if (lo is None and watched >= SIG_VWX_QUIET*60) or (lo is not None and t-lo >= SIG_VWX_QUIET*60):
            VWX["ev"] = dict(st=st, t=t, det=det1, quiet=(None if lo is None else (t-lo)/60)); VWX["ign"] = None
        else: VWX["ign"] = dict(st=st, t=t, why=("watching for repeats since start-up" if lo is None else f"signal 1 also turned {st} {max(1, round((t-lo)/60))} min earlier"))
        VWX["last_on"][st] = t
    VWX["prev"] = st
def sig_vwx():
    """14. VWAP first flip after a quiet spell. Built from what scored best in the 26 Oct research data: signal 1 (futures VWAP + slope)
    turning BULL or BEAR when signal 1 had NOT turned that same way in the previous SIG_VWX_QUIET minutes. Repeat flips during choppy
    trade are ignored. The bias is held SIG_VWX_HOLD minutes (the 15-min horizon where it worked), or until a first flip the other way.
    Signal 1 is sampled once a second, the same way the research logger saw it."""
    raw = SIG_RAW.get("vwap"); now = time.time(); _vwx_track(None)
    if not FUT_KEY: return "NA", "no futures contract found"
    hh = lambda x: datetime.fromtimestamp(x, IST).strftime("%H:%M:%S")
    if now-VWX["start"] < SIG_VWX_QUIET*60 and not VWX["ev"]:
        return "NA", f"watching signal 1 for {SIG_VWX_QUIET} min after start-up before a flip can count as 'first' ({int((now-VWX['start'])//60)}/{SIG_VWX_QUIET} min)"
    ev = VWX["ev"]; quiet_age = None
    if ev and now-ev["t"] < SIG_VWX_HOLD*60:
        quiet_age = ev["quiet"]; left = SIG_VWX_HOLD-(now-ev["t"])/60
        q = f"{quiet_age:.0f} min since its last {ev['st']} turn" if quiet_age is not None else f"no {ev['st']} turn since watching began"
        return ev["st"], (f"First {'bullish' if ev['st'] == 'BULL' else 'bearish'} VWAP flip at {hh(ev['t'])} ({q}; needs {SIG_VWX_QUIET}). "
                          f"Bias held {SIG_VWX_HOLD} min, {left:.0f} min left. At the flip: {ev['det']}")
    if ev: last = f"Last first flip: {ev['st']} at {hh(ev['t'])}, its {SIG_VWX_HOLD}-min hold has finished"
    else: last = "No first flip yet today"
    ig = VWX["ign"]
    if ig and (not ev or ig["t"] > ev["t"]): last += f". Ignored a repeat {ig['st']} flip at {hh(ig['t'])}: {ig['why']}"
    return "NEUTRAL", last + f". Signal 1 now: {raw[0] if raw else 'NA'}"

SIGDEF.append(("vwx", "14. VWAP first flip (after a quiet spell)", "SIG_USE_VWX", "Follows signal 1 (futures VWAP + slope). Bullish: signal 1 turns bullish when it had not turned bullish in the quiet minutes before. Bearish: the same for bearish. Repeat flips in choppy trade are ignored. The bias is held for the set minutes (15 = where it worked in the research data) or until a first flip the other way."))

def run_signals(nw, wall, qs):
    global SIGDATA, SIG_LAST_ENTRY, SIG_STABLE, SR_CACHE, SR_LAST
    if wall-getattr(run_signals, "t", 0) < 0.9: return
    global OI_CACHE, OI_LAST
    if wall-OI_LAST >= 60 and (market_open(nw) or not OI_CACHE):
        OI_LAST = wall
        try:
            OI_CACHE = dict(rows=fetch_oi(EXPIRY0), t=wall)
            if RECORD_TICKS: record_oi(OI_CACHE["rows"], wall)
        except Exception as e: print("open-interest fetch failed:", e)
    run_signals.t = wall; fns = dict(vwap=sig_vwap, rvol=sig_rvol, orb=sig_orb, struct=sig_struct, ema=sig_ema, oi=sig_oi, contrib=sig_contrib, delta=sig_delta, sweep=sig_sweep, vwband=sig_vwband, orbv=sig_orbv, dtrend=sig_dtrend, dma=sig_dma, vwx=sig_vwx); items = []; mo = market_open(nw)
    for key, name, use, rule in SIGDEF:
        rf = max(0, int(globals().get("SIG_RF_"+key.upper(), 5) or 0)); raw = SIG_RAW.get(key)
        if key == "vwap" and rf == 0: due = raw is None or wall-raw[2] > 3               # ticks keep it fresh; this only fills gaps (no ticks / market closed)
        else: due = raw is None or wall-raw[2] >= max(1, rf)
        if due: raw = _sig_eval(key, fns, wall)
        st, det = raw[0], raw[1]
        if key == "vwap": _vwx_track(raw)                                     # signal 14 follows signal 1 every cycle
        items.append(dict(key=key, name=name, use=use, rule=rule, state=st, detail=det, enabled=bool(globals()[use]), predict=PRED[st]))
    en = [i["state"] for i in items if i["enabled"]]; nb, nr = en.count("BULL"), en.count("BEAR")
    if not en: v = "NEUTRAL"
    elif SIG_COMBINE == "all": v = "BULL" if nb == len(en) else "BEAR" if nr == len(en) else "NEUTRAL"
    elif SIG_COMBINE == "any": v = "BULL" if nb and not nr else "BEAR" if nr and not nb else "NEUTRAL"
    else: v = "BULL" if nb > nr and nb > len(en)/2 else "BEAR" if nr > nb and nr > len(en)/2 else "NEUTRAL"
    vd = f"{nb} bullish, {nr} bearish, {len(en)-nb-nr} neutral/no-data out of {len(en)} ticked ({SIG_COMBINE})"
    for key, name, st, det in [(i["key"], i["name"], i["state"], i["detail"]) for i in items]+[("verdict", "COMBINED VERDICT", v, vd)]:
        prev = SIG_PREV.get(key); SIG_PREV[key] = st
        if prev is not None and prev != st and mo:
            if st in ("BULL", "BEAR"): record_signal(key, name, st, spot_of(qs), wall)
            SIG_LOG.insert(0, dict(t=nw.strftime("%H:%M:%S"), name=name, state=st, detail=det)); del SIG_LOG[60:]
            if SIG_NOTIFY and (st in ("BULL", "BEAR") or prev in ("BULL", "BEAR")):
                say(f"SIGNAL {name}: {LBL[st]}. {PRED[st]}"); log(f"SIGNAL {name}: {prev} -> {st} | {det}")
    fq = qs.get(FUT_KEY) or {}
    with clock: fd = dict(CANDLES.get(FUT_KEY, {})) if FUT_KEY else {}
    fvol = sum((c[4] if len(c) > 4 else 0) for m, c in fd.items() if (m+SHIFT)//86400 == _today())
    sp = spot_of(qs)
    if sp and wall-SR_LAST >= 20:
        SR_LAST = wall
        try: SR_CACHE = compute_levels(sp)
        except Exception as e: print("support/resistance error:", e)
    elif sp and SR_CACHE: SR_CACHE["spot"] = sp
    SIGDATA = dict(levels=SR_CACHE, items=items, verdict=v, detail=vd, log=list(SIG_LOG), updated=nw.strftime("%H:%M:%S"),
                   fut=dict(key=FUT_KEY, ltp=fq.get("ltp"), vol=fvol, age=(wall-fq["t"]) if fq.get("t") else None))
    if RECORD_TICKS: record_signals(wall, qs, items, v)
    autolab.safe_hook(nw, wall, qs, items, v)      # Auto Lab (separate paper account): never raises
    _sr_safe_hook(nw, wall, qs, items, v, vd)      # signal research logger: read-only, never raises (placed before the code below that can raise)
    if SR_CACHE and SIG_NOTIFY and mo and sp:
        for kind, arr in (("resistance", SR_CACHE["resist"][:1]), ("support", SR_CACHE["support"][:1])):
            for x in arr:
                kk = round(x["price"]/10)
                if abs(sp-x["price"]) <= SR_CACHE["near"] and wall-SR_ALERTED.get(kk, 0) > 900:
                    SR_ALERTED[kk] = wall; say(f"SIGNAL Price is at {kind} {x['price']:,.0f}: it has turned back from here {x['touches']} time(s) ({', '.join(x['tfs'])})")
    if SIG_EXIT_ON_OPPOSITE and v in ("BULL", "BEAR"):
        for p_ in list(POSITIONS):
            if p_["status"] == "OPEN" and ((p_.get("side") == "PUT" and v == "BEAR") or (p_.get("side") == "CALL" and v == "BULL")):
                with use(p_):
                    m = mark(qs)
                    if m: do_exit(nw, m, "SIGNAL: verdict flipped against the spread"); say(f"Exited {p_['pid']} on opposite signal. Net {money(m['net'])}")
    if v not in ("BULL", "BEAR"): SIG_STABLE = (None, 0.0)
    elif SIG_STABLE[0] != v: SIG_STABLE = (v, wall)
    want = "PUT" if v == "BULL" else "CALL"; n_side = n_live(want)
    n_open = n_live()
    if (SIG_AUTO_TRADE and mo and v in ("BULL", "BEAR") and n_side < SIG_MAX_PER_SIDE and n_open < MAX_OPEN_TRADES and hm("09:30") <= mins(nw) < hm(ENTRY_CUTOFF)
            and session["n"] < MAX_TRADES_PER_DAY and wall-SIG_STABLE[1] >= 30 and wall-SIG_LAST_ENTRY >= SIG_COOLDOWN_MIN*60):
        SIG_LAST_ENTRY = wall; cmdq.put(("ENTER", "SIG:"+(("BUY" if v == "BULL" else "SELL") if TRADE_MODE == "futures" else want)))
        say(f"Signals {LBL[v]} -> entering " + (("BUY futures" if v == "BULL" else "SELL futures") if TRADE_MODE == "futures" else f"a {'BULL PUT' if v == 'BULL' else 'BEAR CALL'} spread"))

def new_position(side=None, force=False, origin="manual"):
    if BACKTEST_MODE: say("Backtest mode is on, so no new live trades. Turn it off in Settings > General."); return None
    if TRADE_MODE == "futures" and side not in ("BUY", "SELL"): side = {"PUT": "BUY", "CALL": "SELL"}.get(side or cur_side(), "BUY")
    if side in ("BUY", "SELL"): return new_single(side, force, origin)
    side = side or cur_side()
    try: _ex = trade_expiry(); newp = pick_legs(fetch_chain(_ex, side), side); newp["expiry"] = str(_ex)
    except Exception as e: say(f"Could not fetch option chain: {e}"); return None
    _need = abs(newp["short"]["strike"]-newp["long"]["strike"])*new_state(newp)["qty"]; _have = acct_available()
    if _need > _have: say(f"Not enough paper funds: this spread needs ~Rs{_need:,.0f} margin, available Rs{_have:,.0f}. Lower the lots or reset with more equity."); return None
    POSITIONS[:] = [p_ for p_ in POSITIONS if not p_.get("idle")]
    PID[0] += 1; ns = new_state(newp); ns.update(pid=PID[0], origin=origin)
    if force: ns["force"] = True; ns["force_until"] = time.time()+45
    POSITIONS.append(ns); SEL["pid"] = ns["pid"]
    with use(ns): log(f"New {'bear call' if side == 'CALL' else 'bull put'} trade #{ns['pid']} planned: SELL {newp['short']['strike']:.0f} / BUY {newp['long']['strike']:.0f}")
    set_cur_legs(); return ns

def exit_positions(nw, qs, which):
    tg = [p_ for p_ in POSITIONS if p_["status"] in ("OPEN", "WAITING") and not p_.get("idle") and (which == "ALL" or p_.get("pid") == which or (which is None and p_.get("pid") == SEL["pid"]))]
    if not tg: say("No open trade to square off"); return
    for p_ in tg:
        with use(p_):
            if p_["status"] == "WAITING": POSITIONS.remove(p_); say(f"Cancelled pending trade #{p_['pid']}"); continue
            m = mark(qs)
            if not m: say(f"No live quotes for trade #{p_['pid']}, cannot price the exit"); continue
            do_exit(nw, m, "MANUAL square-off"); say(f"Squared off #{p_['pid']}. Net {money(m['net'])} (after all charges).")

def delete_position(nw, pid):
    """Remove a trade completely, as if it was never taken: no exit is placed, its rows are removed from trades.csv and
    trade_events.csv, and any P&L it had already booked (full or partial exits) is taken back out of the account and today's session."""
    try: pid = int(pid)
    except Exception: say("Delete: no trade selected"); return
    p_ = next((x for x in POSITIONS if x.get("pid") == pid), None)
    if p_ is None: say(f"Delete: trade #{pid} not found"); return
    tid = p_.get("trade_id"); today = str(nw.date()); undo_all = undo_today = 0.0; undo_n = 0
    if tid:
        mine = lambda t: str(t) == tid or str(t).startswith(tid + "-P")
        if os.path.exists(LEDGER):
            try:
                df = pd.read_csv(LEDGER); hit = df["trade_id"].astype(str).map(mine)
                for _, r in df[hit].iterrows():
                    final = str(r["trade_id"]) == tid
                    if final and not p_.get("_booked"): continue          # exit written but not yet added to the account
                    net = float(r["net_pnl"]) if r["net_pnl"] == r["net_pnl"] else 0.0
                    undo_all += net
                    if str(r["exit_time"]).startswith(today) and session.get("date") == today:
                        undo_today += net; undo_n += 1 if final else 0
                tmp = LEDGER + ".tmp"; df[~hit].to_csv(tmp, index=False); os.replace(tmp, LEDGER)
            except Exception as e: say(f"Delete: could not update trades.csv ({e})"); return
        if os.path.exists(EVENTS):
            try:
                ev = pd.read_csv(EVENTS); tmp = EVENTS + ".tmp"
                ev[~ev["trade_id"].astype(str).map(mine)].to_csv(tmp, index=False); os.replace(tmp, EVENTS)
            except Exception as e: print("delete: could not update trade_events.csv:", e)
    POSITIONS.remove(p_)
    if SEL.get("pid") == pid: SEL["pid"] = next((x.get("pid") for x in POSITIONS), None)
    ACCOUNT["realized"] -= undo_all; session["realized"] -= undo_today; session["n"] = max(0, session.get("n", 0) - undo_n)
    save_account(); save_session(); save_state()
    log(f"DELETED trade #{pid} {tid or '(pending)'} | removed booked P&L {money(undo_all)}")
    say(f"Deleted trade #{pid} completely." + (f" Removed its booked P&L of {money(undo_all)} from the account." if undo_all else ""))

def engine():
    global AUTO, REPLAN_NOW, session
    reenter = []; last_save = last_replan = time.time()
    try:
        while not ENGINE_STOP.is_set():
            try:
                nw, wall = now_ist(), time.time()
                with lock: qs = {k: dict(v) for k, v in quotes.items()}
                if session.get("date") != str(nw.date()): session = dict(date=str(nw.date()), realized=0.0, n=0); save_session()
                while not cmdq.empty():
                    c = cmdq.get(); sarg = None
                    if isinstance(c, tuple): c, sarg = c
                    if c == "AUTO_ON": AUTO = True; save_settings(); say("Auto re-enter ON")
                    elif c == "AUTO_OFF": AUTO = False; save_settings(); reenter.clear(); say("Auto re-enter OFF")
                    elif c == "STOP": ENGINE_STOP.set()
                    elif c == "CLEAR": clear_all(sarg); reenter.clear()
                    elif c == "EXIT": exit_positions(nw, qs, sarg)
                    elif c == "DELETE": delete_position(nw, sarg)
                    elif c == "PARTIAL": partial_exit(nw, qs, sarg[0], sarg[1], "MANUAL partial exit")
                    elif c == "ENTER":
                        sd = str(sarg or "").replace("SIG:", "") or None; org = "signal" if str(sarg or "").startswith("SIG:") else "manual"
                        if n_live() >= MAX_OPEN_TRADES: say(f"Already {MAX_OPEN_TRADES} trades open (your limit in General settings)")
                        elif new_position(sd, force=True, origin=org): say("Entering a new trade with fresh strikes…")
                replan = (wall-last_replan > 60 or REPLAN_NOW) and feed_open and market_open(nw)
                for pos in list(POSITIONS):
                    with use(pos):
                        step(nw, wall, qs)
                        if pos["status"] == "WAITING" and pos.get("force") and wall > pos.get("force_until", 0):
                            POSITIONS.remove(pos); say("Could not enter: check market hours, the no-new-trades time and live quotes"); continue
                        if pos["status"] == "OPEN": pos.pop("force", None); pos.pop("force_until", None)
                        if pos["status"] == "WAITING" and not pos.get("force") and not pos.get("idle") and replan:
                            try:
                                _sd = pos["plan"].get("side", "PUT"); _ex = pos.get("expiry") or str(EXPIRY0); pos["plan"] = pick_legs(fetch_chain(_ex, _sd), _sd); pos["plan"]["expiry"] = _ex
                            except Exception as e: print("replan failed:", e)
                if replan: last_replan = wall; REPLAN_NOW = False
                eval_triggers(nw, wall, qs)
                run_signals(nw, wall, qs); resolve_scores(wall)
                for pos in list(POSITIONS):
                    with use(pos):
                        r = book(); sd = cur_side()
                    if r:
                        if session["n"] >= MAX_TRADES_PER_DAY: say(f"Closed #{pos['pid']} ({r}). Daily trade limit {MAX_TRADES_PER_DAY} reached, no re-entry.")
                        elif AUTO and (r.startswith("MANUAL") or REENTER_AFTER_RULE_EXIT):
                            reenter.append((wall+REENTER_COOLDOWN_SEC, (("BUY" if pos.get("dirn", 1) > 0 else "SELL") if pos.get("kind") == "SINGLE" else sd))); say(f"Closed #{pos['pid']} ({r}). Re-entering per your rules in {REENTER_COOLDOWN_SEC}s")
                        else: say(f"Closed #{pos['pid']} ({r}).")
                for item in list(reenter):
                    if wall >= item[0]:
                        reenter.remove(item)
                        if n_live() < MAX_OPEN_TRADES and new_position(item[1], origin="auto"): say("Re-entry armed: waiting for your entry rules + live quotes")
                closed = [p_ for p_ in POSITIONS if p_["status"] == "CLOSED" and p_.get("pid") != SEL["pid"]]
                for p_ in closed[:-15]: POSITIONS.remove(p_)
                if SEL["pid"] not in [p_.get("pid") for p_ in POSITIONS] and POSITIONS: SEL["pid"] = POSITIONS[-1]["pid"]; set_cur_legs()
                if not POSITIONS: make_idle()
                refresh_pairs(); sync_subs()
                if wall-last_save > 30: save_state(); last_save = wall
                global SNAP; SNAP = build_snapshot(nw, wall, qs)
            except Exception as e:
                say(f"engine error (continuing): {e}")
            time.sleep(1)
    finally:
        save_state(); save_session()
        _sr_shutdown()
        try: flush_ticks()
        except Exception: pass
        try: streamer.disconnect()
        except Exception: pass
        say("Engine stopped. Everything is saved in ./spread_data")

# ------------------------------------------------------------------ web server
app = Flask(__name__); logging.getLogger("werkzeug").setLevel(logging.ERROR)

def series_info(kind):
    st = state
    if st.get("kind") == "SINGLE" and kind in ("short", "long", "spread"):
        k_ = ((st if st.get("status") != "WAITING" else st["plan"])["short"]["key"]); return k_, ("BUY " if st["dirn"] > 0 else "SELL ")+st["ilabel"]
    src = st if st.get("status") not in (None, "WAITING") else st.get("plan", {})
    sk, lk = (src.get("short") or {}), (src.get("long") or {})
    if kind == "spot": return UNDERLYING_KEY, "NIFTY 50 (spot)"
    if kind == "short": return sk.get("key", ""), f"SELL {sk.get('strike', 0):.0f} {OT()}"
    if kind == "long": return lk.get("key", ""), f"BUY {lk.get('strike', 0):.0f} {OT()}"
    return f"SP|{sk.get('key', '')}|{lk.get('key', '')}", f"SPREAD {sk.get('strike', 0):.0f}/{lk.get('strike', 0):.0f} {OT()} (short - long)"

def levels_for(kind):
    st = state; L = []
    if st.get("kind") == "SINGLE":
        if kind != "spot" and st.get("status") == "OPEN":
            e, d, n = st["short"]["entry_px"], st["dirn"], st["qty"]
            L += [dict(price=e, title="ENTRY", color="#9aa7b4"), dict(price=e+d*st["target"]/n, title="TARGET", color="#26a69a"), dict(price=e-d*st["buffer"]/n, title="STOP", color="#ef5350")]
        return L+(sr_chart_levels() if kind == "spot" else [])
    if st.get("status") in (None,): return L
    src = st if st["status"] != "WAITING" else st["plan"]; s, l = src["short"], src["long"]; opn = st["status"] in ("OPEN", "CLOSED")
    if kind == "spot":
        L += [dict(price=s["strike"], title="SHORT strike", color="#ef5350"), dict(price=l["strike"], title="LONG strike", color="#26a69a")]
        if opn: L.append(dict(price=s["strike"]+(1 if cur_side() == "CALL" else -1)*st["credit"], title="Breakeven", color="#f5c542"))
    elif opn and st["status"] == "OPEN":
        q, c, ech, xch = st["qty"], st["credit"], st["entry_charges"], st.get("_exit_ch_est", 60.0)
        if kind == "spread":
            L += [dict(price=c, title="ENTRY credit", color="#9aa7b4"),
                  dict(price=c-(st["target"]+ech+xch)/q, title="TARGET", color="#26a69a"),
                  dict(price=c+(st["buffer"]-ech-xch)/q, title="STOP", color="#ef5350")]
        elif kind == "short": L.append(dict(price=s["entry_px"], title="ENTRY", color="#9aa7b4"))
        elif kind == "long": L.append(dict(price=l["entry_px"], title="ENTRY", color="#9aa7b4"))
    if kind == "spot": L += sr_chart_levels()
    return L

def markers_for(tf):
    st = state; out = []
    if st.get("status") in ("OPEN", "CLOSED") and st.get("entry_ts"):
        out.append(dict(time=int(bucket(int(st["entry_ts"]), tf)), position="belowBar", color="#26a69a", shape="arrowUp", text="ENTRY"))
    if st.get("status") == "CLOSED" and st.get("exit", {}).get("ts"):
        out.append(dict(time=int(bucket(int(st["exit"]["ts"]), tf)), position="aboveBar", color="#ef5350", shape="arrowDown", text="EXIT"))
    return out

@app.route("/")
def index(): return Response(HTML, mimetype="text/html")

@app.route("/api/state")
def api_state(): return jsonify(SNAP)

@app.route("/api/candles")
def api_candles():
    kind = request.args.get("series", "spot"); tf = max(1, min(int(request.args.get("tf", 1)), 240)); tail = request.args.get("tail") == "1"
    key, label = series_info(kind)
    with clock: d = dict(CANDLES.get(key, {})); fd = dict(CANDLES.get(FUT_KEY, {})) if (key == UNDERLYING_KEY and FUT_KEY) else None
    bars = aggregate(d, tf, tail)
    if fd:                                          # the index has no volume of its own: use NIFTY futures volume for the same candles
        fv = {b["time"]: b["volume"] for b in aggregate(fd, tf, tail)}
        for b in bars: b["volume"] = fv.get(b["time"], 0.0)
        label += " · volume = NIFTY futures"
    return jsonify(clean(dict(label=label, tf=tf, bars=bars, levels=levels_for(kind), markers=markers_for(tf))))


@app.route("/api/trades")
def api_trades():
    try: df = pd.read_csv(LEDGER) if os.path.exists(LEDGER) else pd.DataFrame(columns=LEDGER_COLS)
    except Exception: df = pd.DataFrame(columns=LEDGER_COLS)
    n = len(df); net = df["net_pnl"].astype(float) if n else pd.Series(dtype=float)
    st = dict(n=n, wins=int((net > 0).sum()), win_rate=float((net > 0).mean()*100) if n else 0.0, total=float(net.sum()) if n else 0.0,
              charges=float(df["total_charges"].astype(float).sum()) if n else 0.0, best=float(net.max()) if n else 0.0, worst=float(net.min()) if n else 0.0,
              avg=float(net.mean()) if n else 0.0)
    return jsonify(clean(dict(stats=st, rows=df.tail(300).iloc[::-1].fillna("").to_dict("records"))))

@app.route("/api/trades.csv")
def api_trades_csv():
    return Response(open(LEDGER, "rb").read() if os.path.exists(LEDGER) else b"", mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=trades.csv"})

@app.route("/api/events.csv")
def api_events_csv():
    return Response(open(EVENTS, "rb").read() if os.path.exists(EVENTS) else b"", mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=trade_events.csv"})


@app.route("/api/triggers", methods=["POST"])
def api_triggers():
    j = request.json or {}; a = j.get("action")
    with TLOCK:
        if a == "add":
            kind = j.get("series", "spot"); price = float(j["price"]); key, label = series_info(kind)
            act = j.get("act", "ALERT"); d = j.get("dir", "auto")
            if act not in ("EXIT", "ENTER", "ALERT") or kind not in ("spot", "spread", "short", "long"): return jsonify(ok=False), 400
            if d == "auto":
                with lock: qs = {k: dict(v) for k, v in quotes.items()}
                cur = series_price(kind, qs); d = "any" if cur is None else ("up" if price >= cur else "down")
            pid = None
            if act == "EXIT":
                cur_ = next((p_ for p_ in POSITIONS if p_.get("pid") == SEL["pid"]), None)
                if cur_ is None or cur_.get("idle"): say("Select a trade first: an exit line closes only the trade selected on the chart"); return jsonify(ok=False), 400
                pid = cur_["pid"]
            TRIGGERS.append(dict(id=max([t["id"] for t in TRIGGERS], default=0)+1, series=kind, sig=key, label=label, price=price, dir=d, act=act, pid=pid,
                                 once=bool(j.get("once", True)), status="armed", created=str(now_ist()), last=None))
            log(f"Trigger line added: {act} when {label} crosses {price:,.2f} ({d})"); say(f"Trigger line added: {act} at {price:,.2f}" + (f" for trade #{pid} only" if pid else ""))
        elif a == "update":
            for t in TRIGGERS:
                if t["id"] == j.get("id"): t["price"] = float(j["price"]); t["status"] = "armed"; t["last"] = None
        elif a == "delete": TRIGGERS[:] = [t for t in TRIGGERS if t["id"] != j.get("id")]
        elif a == "clear": TRIGGERS.clear()
        save_trigs()
    return jsonify(ok=True)

@app.route("/api/select", methods=["POST"])
def api_select():
    pid = (request.json or {}).get("pid")
    if any(p_.get("pid") == pid for p_ in POSITIONS): SEL["pid"] = pid; set_cur_legs()
    return jsonify(ok=True)

# ------------------------------------------------------------------ SIGNAL SCORECARD (what did Nifty do after each signal?)
SCORE = []; SCORE_PATH = f"{DATA_DIR}/signal_score.json"; SC_LAST = {}; SC_HOR = (15, 30, 60)
try: SCORE[:] = [e for e in (json.load(open(SCORE_PATH)) if os.path.exists(SCORE_PATH) else []) if isinstance(e.get("res"), dict)]       # records saved by other versions of the scorecard are skipped
except Exception: SCORE[:] = []
def save_score(): _atomic(SCORE_PATH, SCORE[-3000:])
def record_signal(key, name, st, spot, wall):
    if not spot or wall-SC_LAST.get((key, st), 0) < 900: return                # the same signal is counted at most once per 15 minutes, so results do not overlap
    SC_LAST[(key, st)] = wall; SCORE.append(dict(id=int(wall*1000), t=wall, key=key, name=name, state=st, price=spot, res={})); save_score()
def resolve_scores(wall):
    with clock: d = dict(CANDLES.get(UNDERLYING_KEY, {}))
    ch = False
    for e in SCORE:
        if len(e["res"]) >= 3: continue
        for H in SC_HOR:
            hk = str(H); tgt = e["t"]+H*60
            if hk in e["res"] or wall < tgt+60: continue
            m = int(tgt//60*60); px = next((d[m-b][3] for b in (0, 60, 120, 180) if (m-b) in d), None)
            same = (tgt+SHIFT)//86400 == (e["t"]+SHIFT)//86400 and (tgt+SHIFT) % 86400 <= 55860
            e["res"][hk] = round((px-e["price"])*(1 if e["state"] == "BULL" else -1), 1) if (px is not None and same) else "x"; ch = True
    if ch: save_score()
def score_stats():
    names = {x[0]: x[1] for x in SIGDEF}; names["verdict"] = "OVERALL VIEW (combined)"; out = []
    for key, nm in names.items():
        ev = [e for e in SCORE if e["key"] == key]; row = dict(key=key, name=nm, n=len(ev), bull=sum(e["state"] == "BULL" for e in ev), bear=sum(e["state"] == "BEAR" for e in ev))
        for H in SC_HOR:
            v = [e["res"][str(H)] for e in ev if isinstance(e["res"].get(str(H)), (int, float))]
            dr, dw = sum(x >= SC_MIN_MOVE for x in v), sum(x <= -SC_MIN_MOVE for x in v)         # only clear moves count as right or wrong
            row[f"n{H}"] = dr+dw; row[f"hit{H}"] = dr/(dr+dw)*100 if dr+dw else None; row[f"avg{H}"] = sum(v)/len(v) if v else None
        out.append(row)
    return out

@app.route("/api/scorecard")
def api_scorecard():
    ev = [dict(e, time=datetime.fromtimestamp(e["t"], IST).strftime("%d %b %H:%M")) for e in SCORE[-120:][::-1]]
    return jsonify(clean(dict(rows=score_stats(), events=ev, min=SC_MIN_MOVE)))

@app.route("/api/scorecard/clear", methods=["POST"])
def api_scorecard_clear():
    SCORE.clear(); SC_LAST.clear(); save_score(); return jsonify(ok=True)

def _rp_candle_ticks(day, key):
    rows = []
    try:
        r = requests.get(f"https://api.upstox.com/v3/historical-candle/{urlq(key, safe='')}/minutes/1/{day}/{day}", headers=HDR, timeout=30)
        rows = r.json().get("data", {}).get("candles", []) if r.ok else []
    except Exception as e: print("replay history failed:", e)
    T, Pp = [], []
    for r_ in sorted(rows, key=lambda x: x[0]):
        m = datetime.fromisoformat(r_[0]).timestamp(); o, h, l, c = (float(v) for v in r_[1:5]); path = [o, h, l, c] if c >= o else [o, l, h, c]
        for j, pv in enumerate(path): T.append(m+(0.5, 20, 40, 59)[j]); Pp.append(pv)
    return T, Pp

@app.route("/api/replay/dates")
def api_rp_dates():
    have = sorted(f[6:-4] for f in os.listdir(TICKS_DIR) if f.startswith("ticks_") and f.endswith(".csv"))
    out = [dict(d=x, src="ticks") for x in have]; d = date.today()-timedelta(days=1); n = 0
    while n < 20:
        if d.weekday() < 5 and str(d) not in have: out.append(dict(d=str(d), src="candles")); n += 1
        d -= timedelta(days=1)
    return jsonify(sorted(out, key=lambda x: x["d"], reverse=True))

@app.route("/api/replay/data")
def api_rp_data():
    day = request.args.get("date", ""); key = request.args.get("key") or UNDERLYING_KEY; path = f"{TICKS_DIR}/ticks_{day}.csv"
    if os.path.exists(path):
        try: lab = json.load(open(f"{TICKS_DIR}/keys_{day}.json"))
        except Exception: lab = {}
        lab.setdefault(UNDERLYING_KEY, "NIFTY SPOT")
        df = pd.read_csv(path, usecols=["ts", "key", "ltp"]); have = list(dict.fromkeys(df.key))
        if key not in have: key = UNDERLYING_KEY if UNDERLYING_KEY in have else (have[0] if have else key)
        x = df[(df.key == key) & df.ltp.notna()]; T = [round(v, 3) for v in x.ts.tolist()]; Pp = x.ltp.tolist(); src = "ticks"
        keys = sorted([dict(key=k, label=lab.get(k, KEYLABEL.get(k, k))) for k in have], key=lambda z: (z["key"] != UNDERLYING_KEY, z["label"]))
    else:
        keys = [dict(key=UNDERLYING_KEY, label="NIFTY SPOT")]+([dict(key=FUT_KEY, label="NIFTY FUT")] if FUT_KEY else [])
        if key not in (UNDERLYING_KEY, FUT_KEY): key = UNDERLYING_KEY
        T, Pp = _rp_candle_ticks(day, key); src = "candles"; lab = {k["key"]: k["label"] for k in keys}
    return jsonify(dict(label=lab.get(key, key), key=key, source=src, T=T, P=Pp, keys=keys))

@app.route("/api/cmd", methods=["POST"])
def api_cmd():
    j = request.json or {}; c = j.get("cmd")
    if c == "ENTER" and j.get("side") in ("PUT", "CALL", "BUY", "SELL"): cmdq.put(("ENTER", j["side"])); return jsonify(ok=True)
    if c == "PARTIAL": cmdq.put(("PARTIAL", (j.get("pid"), int(j.get("lots", 1))))); return jsonify(ok=True)
    if c == "EXIT": cmdq.put(("EXIT", j.get("pid", "ALL"))); return jsonify(ok=True)
    if c == "EXPIRY":
        try: e = date.fromisoformat(str(j.get("exp")))
        except Exception: return jsonify(ok=False), 400
        if e not in exp_choices(): return jsonify(ok=False), 400
        TRADE_EXP[0] = None if e == EXPIRY0 else e; say(f"New trades will use the {e:%d %b %Y} expiry"); return jsonify(ok=True)
    if c == "DELETE" and j.get("pid") is not None: cmdq.put(("DELETE", j.get("pid"))); return jsonify(ok=True)
    if c in ("ENTER", "AUTO_ON", "AUTO_OFF", "STOP"): cmdq.put(c); return jsonify(ok=True)
    return jsonify(ok=False), 400

@app.route("/api/account/reset", methods=["POST"])
def api_account_reset():
    try: eq = float((request.json or {}).get("equity"))
    except Exception: return jsonify(ok=False, error="bad equity"), 400
    if not (1000 <= eq <= 1e10): return jsonify(ok=False, error="equity must be between 1,000 and 10,000,000,000"), 400
    cmdq.put(("CLEAR", eq)); return jsonify(ok=True)

@app.route("/api/settings", methods=["POST"])
def api_settings():
    global REPLAN_NOW
    changed = []
    for k, v in (request.json or {}).items():
        t = EDITABLE.get(k)
        if t is None: continue
        try:
            if t is bool: val = bool(v)
            elif t in (int, float): val = t(v)
            elif isinstance(t, tuple):
                assert v in t; val = v
            elif t == "time": hm(v); val = v
            else:
                assert v == "now" or hm(v) >= 0; val = v
        except Exception: say(f"Rejected invalid value for {k}: {v}"); continue
        if globals()[k] != val: globals()[k] = val; changed.append(f"{k}={val}")
    if changed: save_settings(); log("Settings changed: " + ", ".join(changed)); REPLAN_NOW = True; say("Saved. Strike and lot choices apply to your next trade.")
    return jsonify(ok=True)

# ------------------------------------------------------------------ SIGNAL RESEARCH LOGGER (read-only observer for later performance analysis)
# It never changes a signal, the verdict, a trade or the dashboard. It only reads what the signal engine already computed
# and appends rows to ./signal_research/. Every function is wrapped so an error here can never stop the engine.
SIGNAL_RESEARCH_ON = True
SR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_research")
SR_SCHEMA = 1
SR_HORIZONS = (1, 5, 15, 30)                  # minutes after the original signal timestamp
SR_INSTR = ("SPOT", "FUT")                    # every horizon is measured on NIFTY spot and on the NIFTY futures contract
SR_LIVE_MAX_LAG = 5.0                         # a live price counts as "the price at the horizon" only if seen within 5 s after it
SR_LIVE_MAX_GAP = 10.0                        # live excursions are trusted only if no gap between price samples was longer than this
SR_STALE_QUOTE = 15.0                         # a quote whose last tick is older than this is never used
SR_GIVE_UP = 900.0                            # no usable price 15 min after a horizon -> UNRESOLVED_NO_DATA (never guessed)
SR_CLOSE_TOD = 15*3600+30*60                  # 15:30 IST: horizons landing at/after this are NOT_REACHED_MARKET_CLOSED
SR_REPORT_EVERY = 600.0
SR_BB = ("BULL", "BEAR")
SR_EVENTS = os.path.join(SR_DIR, "signal_events.csv"); SR_OUTCOMES = os.path.join(SR_DIR, "signal_outcomes.csv")
SR_CHANGES = os.path.join(SR_DIR, "signal_state_changes.csv"); SR_REPORT = os.path.join(SR_DIR, "daily_signal_report.json")
SR_README = os.path.join(SR_DIR, "signal_research_README.txt"); SR_STATE = os.path.join(SR_DIR, "_tracker_state.json")
SR_LOCALS = {}; SR_LOCK = threading.RLock(); SR_T0 = time.time()
SR = dict(loaded=False, last={}, active={}, open={}, trades={}, recent=[], ids_ev=set(), ids_out=set(), ids_chg=set(), hdr={},
          mo_prev=None, first=True, dirty=False, saved=0.0, rep_t=0.0, rep_busy=False, err_t=0.0, seq=0, restored=False)

SR_META = {"vwap": ("NIFTY FUT", "1m candles (re-checked every futures tick)"), "rvol": ("NIFTY FUT", "5m candles"), "orb": ("NIFTY SPOT", "5m closes vs 09:15-09:30 range"),
           "struct": ("NIFTY SPOT", "5m swing points"), "ema": ("NIFTY SPOT", "5m closes"), "oi": ("NIFTY SPOT + option-chain OI", "5m close, OI every 60s"),
           "contrib": ("NIFTY index + heavyweight stocks", "live LTP vs previous close"), "delta": ("NIFTY FUT", "1m candles + estimated delta"),
           "sweep": ("NIFTY SPOT", "1m candles"), "vwband": ("NIFTY FUT", "1m candles"), "orbv": ("NIFTY SPOT + FUT volume", "5m candles"),
           "dtrend": ("NIFTY FUT", "5m candles + estimated delta"), "dma": ("NIFTY SPOT", "daily closes"), "vwx": ("NIFTY FUT", "signal 1 state sampled every ~1s"), "verdict": ("COMBINED", "combination of ticked signals")}
SR_THRESH = {"vwap": ["SIG_SLOPE_MIN", "SIG_VWAP_BUFFER", "SIG_VWAP_COMPRESS", "SIG_VWB_MIN_SIGMA", "SIG_RF_VWAP"], "rvol": ["SIG_RVOL_MIN", "SIG_RF_RVOL"],
             "orb": ["SIG_RF_ORB"], "struct": ["SIG_RF_STRUCT"], "ema": ["SIG_RF_EMA"], "oi": ["SIG_OI_BUFFER", "SIG_RF_OI"], "contrib": ["SIG_CONTRIB_MIN", "NIFTY_WEIGHTS", "SIG_RF_CONTRIB"],
             "delta": ["SIG_DELTA_LOOKBACK", "SIG_DELTA_MIN_BARS", "SIG_DELTA_FRESH", "SIG_RF_DELTA"],
             "sweep": ["SIG_SWEEP_LOOKBACK", "SIG_SWEEP_CLOSE_FRAC", "SIG_SWEEP_MIN_PTS", "SIG_SWEEP_MIN_DEPTH", "SIG_RF_SWEEP"],
             "vwband": ["SIG_VWB_MIN_BARS", "SIG_VWB_MIN_SIGMA", "SIG_VWB_EXT", "SIG_VWB_SLOPE_MIN", "SIG_VWB_FLAT", "SIG_VWB_WINDOW", "SIG_RF_VWBAND"],
             "orbv": ["SIG_RVOL_MIN", "SIG_RF_ORBV"], "dtrend": ["SIG_DT_BARS", "SIG_DT_MIN_RATIO", "SIG_RF_DTREND"], "dma": ["DMA_FAST", "DMA_SLOW", "DMA_LONG", "SIG_RF_DMA"], "vwx": ["SIG_VWX_QUIET", "SIG_VWX_HOLD", "SIG_RF_VWX", "SIG_SLOPE_MIN", "SIG_VWAP_BUFFER", "SIG_VWAP_COMPRESS"],
             "verdict": ["SIG_COMBINE"]}
SR_COMMON = ["SIG_COMBINE", "SIG_AUTO_TRADE", "SIG_COOLDOWN_MIN", "SIG_EXIT_ON_OPPOSITE", "SIG_MAX_PER_SIDE", "SC_MIN_MOVE", "TRADE_MODE", "FUT_TARGET_PTS", "FUT_STOP_PTS"]

SR_EV_COLS = ["schema_version", "event_id", "trading_day", "signal_key", "signal_number", "signal_name", "enabled_in_verdict", "signal_instrument", "signal_timeframe",
    "direction", "direction_sign", "prev_state", "trigger_type", "onset_quality", "signal_eval_ts", "signal_eval_time_ist", "detected_ts", "detected_time_ist", "detection_lag_s",
    "minutes_since_open", "process_uptime_s", "ref_spot", "ref_spot_tick_age_s", "ref_fut", "ref_fut_tick_age_s", "fut_key", "fut_basis", "trigger_detail", "rule_text",
    "strength_label", "strength_value", "strength_basis", "thresholds_json", "indicator_values_json", "snapshot_status", "snapshot_eval_ts", "verdict_state", "verdict_detail",
    "combine_mode", "n_enabled", "n_agree_enabled", "n_oppose_enabled", "n_agree_all", "n_oppose_all", "agreeing_signals", "opposing_signals", "other_signals_json",
    "day_open", "day_high", "day_low", "prev_close", "day_change_pct", "atr5_pts", "vwap", "vwap_sigma", "sr_resistance_1", "sr_support_1", "levels_json",
    "market_context_json", "open_positions_json", "auto_trade_enabled", "auto_trade_blockers", "seconds_since_prev_same_event", "recent_candles_json", "missing_data_flags"]
SR_OUT_COLS = ["schema_version", "outcome_id", "event_id", "trading_day", "signal_key", "signal_name", "direction", "onset_quality", "horizon_min", "instrument", "status",
    "finalized_ts", "finalized_time_ist", "signal_eval_ts", "target_ts", "target_time_ist", "ref_price", "obs_price", "obs_method", "obs_tick_ts", "obs_time_error_s",
    "move_pts", "move_pct", "signed_move_pts", "signed_move_pct", "flat_threshold_pts", "result", "raw_direction_correct", "mfe_pts", "mae_pts", "excursion_method",
    "live_mfe_pts", "live_mae_pts", "live_t_mfe_s", "live_t_mae_s", "live_samples", "live_max_gap_s", "tracking_interrupted", "candle_mfe_pts", "candle_mae_pts",
    "candle_bars_used", "candle_bars_expected", "rule_target_pts", "rule_stop_pts", "target_touched", "stop_touched", "first_touch", "t_target_s", "t_stop_s",
    "sr_favorable_level", "sr_favorable_touched", "sr_adverse_level", "sr_adverse_touched", "signal_changes_in_window", "signal_state_at_horizon", "reversed_in_window",
    "t_reversal_s", "invalidated_in_window", "t_invalidated_s", "verdict_at_horizon", "max_opposing_enabled", "max_opposing_all", "other_events_same_dir",
    "other_events_opp_dir", "other_event_keys", "missing_data_flags"]
SR_CHG_COLS = ["schema_version", "change_id", "ts", "time_ist", "trading_day", "change_type", "signal_key", "signal_name", "event_id", "prev_state", "new_state",
    "prev_strength", "new_strength", "reason_detail", "seconds_since_event", "spot", "fut", "enabled_in_verdict", "n_agree_enabled", "n_oppose_enabled", "verdict_state",
    "all_states", "affected_events_json", "trade_id", "trade_role", "trade_side", "trade_origin", "trade_gross_pnl", "trade_net_pnl", "trade_exit_reason", "extra_json"]


# ---------- small helpers
def _sr_r(x, n=2):
    try:
        if x is None or isinstance(x, bool): return x
        x = float(x); return None if (math.isnan(x) or math.isinf(x)) else round(x, n)
    except Exception: return None
def _sr_tist(ts):
    try: return datetime.fromtimestamp(float(ts), IST).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except Exception: return ""
def _sr_tod(ts): return (int(ts)+SHIFT) % 86400
def _sr_j(o):
    try: return json.dumps(clean(o), default=str, separators=(",", ":"))
    except Exception as e: return json.dumps({"_json_error": str(e)})
def _sr_err(where, e):
    if time.time()-SR["err_t"] > 30: SR["err_t"] = time.time(); print(f"signal research ({where}) skipped: {e}")
def _sr_sign(d): return 1 if d == "BULL" else -1
def _sr_opp(d): return "BEAR" if d == "BULL" else "BULL"

def _sr_append(path, cols, row):
    """Append-only CSV writer. The header is written once; an existing file with a different header is never edited:
    it is renamed *_schemaold_<time>.csv and a fresh file is started (no row is ever deleted or rewritten)."""
    if path not in SR["hdr"]:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, newline="", encoding="utf-8") as fh: head = next(csv.reader(fh), [])
            if head != cols: os.replace(path, path.replace(".csv", f"_schemaold_{int(time.time())}.csv"))
        SR["hdr"][path] = True
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", restval="")
        if new: w.writeheader()
        w.writerow({k: ("" if v is None else (int(v) if isinstance(v, bool) else v)) for k, v in row.items()})

def _sr_read(path):
    if not os.path.exists(path): return []
    csv.field_size_limit(10**8)
    with open(path, newline="", encoding="utf-8") as fh: return [r for r in csv.DictReader(fh) if None not in r.values()]

def _sr_chg(wall, ctype, **kw):
    SR["seq"] += 1; cid = kw.pop("change_id", None) or f"C{int(wall*1000)}-{SR['seq']}"
    if cid in SR["ids_chg"]: return
    SR["ids_chg"].add(cid)
    _sr_append(SR_CHANGES, SR_CHG_COLS, dict(schema_version=SR_SCHEMA, change_id=cid, ts=_sr_r(wall, 3), time_ist=_sr_tist(wall), trading_day=_ist_day(wall), change_type=ctype, **kw))


# ---------- capture of each signal's own internal values at the exact evaluation that produced its state
def _sr_call(fn, key):
    """Runs a signal function exactly as before and, on its return, copies its local variables (the indicator values and
    intermediate conditions it computed). The return value and any exception are passed through unchanged."""
    prev = sys.gettrace()
    if not SIGNAL_RESEARCH_ON or prev is not None: return fn()           # a debugger/profiler is attached: do not interfere
    code = fn.__code__; box = {}
    def _local(frame, event, arg):
        if event == "return":
            try: box["loc"] = dict(frame.f_locals)
            except Exception: pass
        return _local
    def _glob(frame, event, arg):
        if event == "call" and frame.f_code is code and "on" not in box:
            box["on"] = 1
            try: frame.f_trace_lines = False
            except Exception: pass
            return _local
        return None
    try: sys.settrace(_glob)
    except Exception: return fn()
    try: res = fn()
    finally: sys.settrace(prev)
    try:
        if isinstance(res, tuple) and len(res) == 2: SR_LOCALS[key] = (time.time(), res[0], box.get("loc"))
    except Exception: pass
    return res

_SR_NOT = object()
def _sr_scalar(v):
    if v is None or isinstance(v, bool): return v
    if isinstance(v, (np.floating, np.integer, np.bool_)): v = v.item()
    if isinstance(v, bool): return v
    if isinstance(v, int): return v
    if isinstance(v, float): return None if (math.isnan(v) or math.isinf(v)) else round(v, 4)
    if isinstance(v, str): return v[:400]
    return _SR_NOT
def _sr_compact(v, depth=0):
    s = _sr_scalar(v)
    if s is not _SR_NOT: return s
    if depth > 2: return _SR_NOT
    if isinstance(v, dict):
        if len(v) <= 10:
            out = {str(k): _sr_compact(x, depth+1) for k, x in v.items()}
            return {k: x for k, x in out.items() if x is not _SR_NOT}
        return {"__len": len(v)}
    if isinstance(v, (list, tuple, set)):
        v = list(v)
        if len(v) <= 8:
            out = [_sr_compact(x, depth+1) for x in v]; return [x for x in out if x is not _SR_NOT]
        tail = [_sr_compact(x, depth+1) for x in v[-(3 if v and isinstance(v[-1], (dict, list, tuple)) else 6):]]
        return {"__len": len(v), "last": [x for x in tail if x is not _SR_NOT]}
    return _SR_NOT
def _sr_locals(loc):
    if not loc: return {}
    out = {}
    for k, v in loc.items():
        if k.startswith("_") or callable(v): continue
        c = _sr_compact(v)
        if c is not _SR_NOT: out[k] = c
    return out

def _sr_strength(key, st, loc, det):
    """Research-only strength measure read from the signal's own values (not used by any signal or trade rule)."""
    lab = ("STRONG" if "STRONG" in (det or "") else "NORMAL") if st in SR_BB else ""
    loc = loc or {}; s = _sr_sign(st) if st in SR_BB else 0; val = None; basis = ""
    try:
        if key == "vwap": val, basis = loc.get("z"), "sigma distance of futures from VWAP (None when sigma < SIG_VWB_MIN_SIGMA)"
        elif key == "rvol": val, basis = loc.get("r"), "5m futures volume multiple vs usual"
        elif key == "orb": val, basis = ((loc["cl"]-loc["hi"]) if s > 0 else (loc["lo"]-loc["cl"])), "points the 5m close is beyond the opening range"
        elif key == "struct":
            ph, pl = loc["ph"], loc["pl"]; val, basis = s*((ph[-1]-ph[-2])+(pl[-1]-pl[-2]))/2, "avg points swing high/low moved in signal direction"
        elif key == "ema": val, basis = loc["a"]-loc["c"], "EMA9 minus EMA21 (points)"
        elif key == "oi": val, basis = ((loc["cl"]-loc["cw"][0]) if s > 0 else (loc["pw"][0]-loc["cl"])), "points the 5m close is beyond the OI wall"
        elif key == "contrib": val, basis = loc.get("tot"), "index points added by heavyweights"
        elif key == "delta": val, basis = None, "binary divergence (no magnitude in the rule)"
        elif key == "sweep": val, basis = (loc["up"] if s > 0 else 1-loc["up"]), "close position in the sweep candle range (0-1)"
        elif key == "vwband": val, basis = (-loc["zl"] if s > 0 else loc["zh"]), "deepest sigma excursion before the reclaim/loss"
        elif key == "orbv": val, basis = loc["info"][2], "breakout candle volume multiple vs usual"
        elif key == "dtrend": val, basis = loc.get("ratio"), "net delta / volume over the window"
        elif key == "dma": val, basis = (loc["px"]-loc["fast"])/loc["fast"]*100, "% distance of price from the fast DMA"
        elif key == "vwx": val, basis = (loc.get("quiet_age") if st in SR_BB else None), "minutes since signal 1 last turned the same way (None = none seen since watching began)"
        elif key == "verdict": val, basis = loc.get("n_agree"), "number of ticked signals agreeing with the verdict"
    except Exception: val = None
    return lab, _sr_r(val, 4), basis


# ---------- persistence / restart
def _sr_save(force=False):
    if not (force or SR["dirty"]): return
    try:
        _atomic(SR_STATE, dict(schema_version=SR_SCHEMA, saved=time.time(), last=SR["last"], active=SR["active"], open=SR["open"],
                               trades=SR["trades"], recent=SR["recent"][-3000:]))
        SR["dirty"] = False; SR["saved"] = time.time()
    except Exception as e: _sr_err("save", e)

def _sr_rebuild(evrows, outs):
    """If the tracker file was lost, re-create pending events from signal_events.csv so their horizons can still be finalized
    from real 1-minute candles (live excursion tracking for them is marked interrupted)."""
    done = {}
    for o in outs: done.setdefault(o.get("event_id"), []).append(f"{o.get('horizon_min')}|{o.get('instrument')}")
    now = time.time()
    for r in evrows:
        eid = r.get("event_id")
        if not eid or eid in SR["active"]: continue
        try: t0 = float(r["signal_eval_ts"])
        except Exception: continue
        if now-t0 > 2*86400 or len(done.get(eid, [])) >= len(SR_HORIZONS)*len(SR_INSTR): continue
        try: lv = json.loads(r.get("levels_json") or "{}")
        except Exception: lv = {}
        ev = _sr_new_track(eid, r["signal_key"], r["signal_name"], r["direction"], t0, _sr_f(r.get("ref_spot")), _sr_f(r.get("ref_fut")), r.get("fut_key") or "",
                           r.get("onset_quality", ""), lv)
        ev["done"] = done.get(eid, []); ev["intr"] = True; SR["active"][eid] = ev
def _sr_f(x):
    try: return None if x in (None, "") else float(x)
    except Exception: return None

def _sr_load(wall):
    SR["loaded"] = True
    os.makedirs(SR_DIR, exist_ok=True)
    _sr_write_readme()
    evrows, outs = _sr_read(SR_EVENTS), _sr_read(SR_OUTCOMES)
    SR["ids_ev"] = {r.get("event_id") for r in evrows}; SR["ids_out"] = {r.get("outcome_id") for r in outs}
    SR["ids_chg"] = {r.get("change_id") for r in _sr_read(SR_CHANGES) if str(r.get("change_id", "")).startswith("T")}
    if os.path.exists(SR_STATE):
        try:
            s = json.load(open(SR_STATE, encoding="utf-8"))
            for k in ("last", "active", "open", "trades", "recent"): SR[k] = s.get(k) or SR[k]
            for ev in SR["active"].values(): ev["intr"] = True               # live tracking had a gap while the script was not running
            SR["restored"] = True
        except Exception as e: print("signal research tracker state unreadable, rebuilding from CSV:", e)
    _sr_rebuild(evrows, outs)
    today = _ist_day(wall)
    for key, o in list(SR["open"].items()):
        if _ist_day(o["t0"]) != today:
            _sr_chg(wall, "EVENT_ENDED", signal_key=key, event_id=o["id"], prev_state=o["dir"], reason_detail="tracker restarted on a later day: end time unknown",
                    seconds_since_event=_sr_r(wall-o["t0"], 1), extra_json=_sr_j(dict(end_time_unknown=True)))
            SR["open"].pop(key, None)
    SR["last"] = {k: v for k, v in SR["last"].items() if isinstance(v, list) and len(v) >= 4 and v[3] == today}   # only same-day states count as "previous"
    _sr_chg(wall, "TRACKER_START", reason_detail=f"tracker started; restored_state={SR['restored']}; pending_events={len(SR['active'])}; known_trades={len(SR['trades'])}",
            extra_json=_sr_j(dict(pid=os.getpid(), schema=SR_SCHEMA, horizons=SR_HORIZONS)))
    SR["dirty"] = True; _sr_save()


# ---------- context collection (read-only)
def _sr_context(nw, wall, qs, cur):
    ctx = {}; flags = []
    with clock: ds = dict(CANDLES.get(UNDERLYING_KEY, {})); df = dict(CANDLES.get(FUT_KEY, {})) if FUT_KEY else {}; dl = dict(DELTA.get(FUT_KEY, {})) if FUT_KEY else {}
    td = [m for m in sorted(ds) if (m+SHIFT)//86400 == _today()]
    if td: ctx.update(day_open=ds[td[0]][0], day_high=max(ds[m][1] for m in td), day_low=min(ds[m][2] for m in td), spot_minutes_today=len(td))
    else: flags.append("NO_SPOT_CANDLES_TODAY")
    iq = qs.get(UNDERLYING_KEY) or {}
    ctx["prev_close"] = iq.get("cp")
    if iq.get("cp") and iq.get("ltp"): ctx["day_change_pct"] = (iq["ltp"]/iq["cp"]-1)*100
    b5 = [b for b in aggregate(ds, 5) if b["time"]+300 <= int(wall)+SHIFT and b["time"]//86400 == _today()]
    if b5: ctx["atr5_pts"] = sum(b["high"]-b["low"] for b in b5[-6:])/len(b5[-6:]); ctx["atr5_bars"] = len(b5[-6:])
    vl = SR_LOCALS.get("vwap")
    if vl and vl[2]: ctx.update(vwap=vl[2].get("vw"), vwap_sigma=vl[2].get("sd"), vwap_slope=vl[2].get("sl"), vwap_eval_age_s=wall-vl[0])
    ol = SR_LOCALS.get("oi")
    if ol and ol[2] and ol[2].get("cw"): ctx.update(oi_call_wall=ol[2]["cw"][0], oi_call_wall_oi=ol[2]["cw"][1], oi_put_wall=ol[2]["pw"][0], oi_put_wall_oi=ol[2]["pw"][3], oi_age_s=wall-(OI_CACHE.get("t") or wall))
    lv = SR_CACHE or {}
    if lv.get("resist"): ctx["sr_res"] = [dict(price=x["price"], touches=x["touches"], strength=x["strength"], tfs=x["tfs"]) for x in lv["resist"][:3]]
    if lv.get("support"): ctx["sr_sup"] = [dict(price=x["price"], touches=x["touches"], strength=x["strength"], tfs=x["tfs"]) for x in lv["support"][:3]]
    try: ctx.update(expiry=str(EXPIRY0), dte=(EXPIRY0-nw.date()).days if EXPIRY0 else None)
    except Exception: pass
    fq = qs.get(FUT_KEY) or {}
    ctx.update(weekday=nw.strftime("%a"), minutes_since_open=(mins(nw)-hm("09:15")), trade_mode=TRADE_MODE, backtest_mode=BACKTEST_MODE, rest_mode=REST_MODE,
               session_trades=session.get("n"), session_realized=session.get("realized"), open_trades=n_live(), spot_tick_age_s=(wall-iq["t"]) if iq.get("t") else None,
               fut_tick_age_s=(wall-fq["t"]) if fq.get("t") else None, fut_bid=fq.get("bid"), fut_ask=fq.get("ask"), spot_ltp=iq.get("ltp"), fut_ltp=fq.get("ltp"),
               fut_volume_today=sum((c[4] if len(c) > 4 else 0) for m, c in df.items() if (m+SHIFT)//86400 == _today()))
    if not FUT_KEY: flags.append("NO_FUTURES_CONTRACT")
    # underlying data so the trigger can be investigated later without recomputing anything from later data
    rc = dict(spot_1m=[[b["time"]-SHIFT, b["open"], b["high"], b["low"], b["close"]] for b in aggregate({m: ds[m] for m in td[-31:]}, 1)][-30:],
              fut_1m=[[m, *[_sr_r(x, 2) for x in df[m][:5]]] for m in sorted(df)[-30:]],
              fut_5m=[[b["time"]-SHIFT, b["open"], b["high"], b["low"], b["close"], b["volume"]] for b in aggregate({m: df[m] for m in sorted(df)[-90:]}, 5)][-12:],
              fut_delta_1m=[[m, _sr_r(dl[m], 1)] for m in sorted(dl)[-30:]],
              columns=dict(spot_1m="[epoch_utc_minute_start, open, high, low, close] (spot index has no volume)", fut_1m="[epoch, open, high, low, close, volume]",
                           fut_5m="[epoch_start, open, high, low, close, volume]", fut_delta_1m="[epoch_minute, estimated signed volume]"),
              note="last candle may still be forming at the event time")
    if not dl: flags.append("NO_DELTA_DATA")
    return ctx, rc, flags

def _sr_blockers(nw, wall, key, st, v):
    mo = market_open(nw); want = "PUT" if st == "BULL" else "CALL"; b = []
    if key != "verdict" and v != st: b.append(f"combined verdict is {v}: only the verdict can open signal trades")
    if not SIG_AUTO_TRADE: b.append("SIG_AUTO_TRADE is off")
    if BACKTEST_MODE: b.append("BACKTEST_MODE blocks new trades")
    if not mo: b.append("market closed")
    if not (hm("09:30") <= mins(nw) < hm(ENTRY_CUTOFF)): b.append(f"outside 09:30-{ENTRY_CUTOFF}")
    if n_live(want) >= SIG_MAX_PER_SIDE: b.append(f"already {n_live(want)} {want} trade(s) open (SIG_MAX_PER_SIDE={SIG_MAX_PER_SIDE})")
    if n_live() >= MAX_OPEN_TRADES: b.append("MAX_OPEN_TRADES reached")
    if session.get("n", 0) >= MAX_TRADES_PER_DAY: b.append("MAX_TRADES_PER_DAY reached")
    if wall-SIG_LAST_ENTRY < SIG_COOLDOWN_MIN*60: b.append(f"cooldown: last signal entry {wall-SIG_LAST_ENTRY:.0f}s ago (needs {SIG_COOLDOWN_MIN*60}s)")
    if key == "verdict": b.append("verdict must stay unchanged for 30s before an entry (normal wait, not a block)")
    return b


# ---------- event creation
def _sr_new_track(eid, key, name, d, t0, rspot, rfut, fk, onset, levels):
    lvd = {}
    for inst, ref in (("SPOT", rspot), ("FUT", rfut)):
        if ref: lvd[inst] = dict(ref=ref, fmax=0.0, fmin=0.0, tmax=0.0, tmin=0.0, n=0, last=None, gap=0.0, tt=None, ts=None, tsf=None, tsa=None)
    return dict(id=eid, key=key, name=name, dir=d, t0=t0, day=_ist_day(t0), fk=fk, onset=onset, lv=lvd, snap={}, done=[], chg=[], opp=[0, 0], intr=False,
                T=_sr_f((levels.get("rule") or {}).get("target_pts")), S=_sr_f((levels.get("rule") or {}).get("stop_pts")),
                srf=_sr_f(levels.get("sr_favorable")), sra=_sr_f(levels.get("sr_adverse")), end=None, verdict0=None)

def _sr_event(nw, wall, qs, key, st, det, en, name, prev, ttype, onset, cur, v, vd, rule):
    raw = SIG_RAW.get(key); t0 = raw[2] if (key != "verdict" and raw and raw[0] == st) else wall
    eid = f"{_ist_day(t0)}-{key}-{st}-{int(t0*1000)}"
    if eid in SR["ids_ev"]: return
    flags = []
    L = SR_LOCALS.get(key)
    if key == "verdict":
        en_ = [k for k, x in cur.items() if k != "verdict" and x[2]]
        loc = dict(n_bull=sum(cur[k][0] == "BULL" for k in en_), n_bear=sum(cur[k][0] == "BEAR" for k in en_), n_enabled=len(en_), combine=SIG_COMBINE,
                   enabled={k: cur[k][0] for k in en_}, n_agree=sum(cur[k][0] == st for k in en_)); snap, sts = "BUILT_FROM_ENABLED_STATES", wall
    elif L and L[1] == st and L[2] is not None: loc, snap, sts = L[2], "CAPTURED_AT_EVALUATION", L[0]
    elif L and L[1] == st: loc, snap, sts = {}, "LOCALS_UNAVAILABLE", L[0]; flags.append("INDICATOR_VALUES_UNAVAILABLE")
    else: loc, snap, sts = {}, "STATE_MISMATCH" if L else "NOT_CAPTURED", (L[0] if L else None); flags.append("INDICATOR_VALUES_UNAVAILABLE")
    lab, sval, sbasis = _sr_strength(key, st, loc, det)
    iq, fq = qs.get(UNDERLYING_KEY) or {}, qs.get(FUT_KEY) or {}
    rspot = iq.get("ltp") if iq.get("t") and wall-iq["t"] <= SR_STALE_QUOTE else None
    rfut = fq.get("ltp") if FUT_KEY and fq.get("t") and wall-fq["t"] <= SR_STALE_QUOTE else None
    if rspot is None: flags.append("NO_FRESH_SPOT_REFERENCE")
    if rfut is None: flags.append("NO_FRESH_FUT_REFERENCE")
    others = {k: dict(state=x[0], enabled=x[2], detail=x[1][:300]) for k, x in cur.items() if k != key}
    pool = [k for k in cur if k not in (key, "verdict")]
    ag_all = [k for k in pool if cur[k][0] == st]; op_all = [k for k in pool if cur[k][0] == _sr_opp(st)]
    ag_en = [k for k in ag_all if cur[k][2]]; op_en = [k for k in op_all if cur[k][2]]
    ctx, rc, f2 = _sr_context(nw, wall, qs, cur); flags += f2
    thr = {k: globals().get(k) for k in SR_THRESH.get(key, [])+SR_COMMON}
    s = _sr_sign(st); res1 = (ctx.get("sr_res") or [{}])[0].get("price"); sup1 = (ctx.get("sr_sup") or [{}])[0].get("price")
    srf, sra = (res1, sup1) if st == "BULL" else (sup1, res1)
    sel = None
    try:
        p = next((p_ for p_ in POSITIONS if p_.get("pid") == SEL["pid"]), None)
        if p:
            src = p if p.get("status") != "WAITING" else p.get("plan", {})
            sel = dict(pid=p.get("pid"), status=p.get("status"), side=p.get("side"), short_strike=(src.get("short") or {}).get("strike"), long_strike=(src.get("long") or {}).get("strike"))
    except Exception: pass
    levels = dict(rule=dict(basis="existing futures-trade rule FUT_TARGET_PTS / FUT_STOP_PTS applied to the signal direction (hypothetical: the signal itself has no target or stop)",
                            target_pts=FUT_TARGET_PTS, stop_pts=FUT_STOP_PTS,
                            fut_target=(rfut+s*FUT_TARGET_PTS) if rfut else None, fut_stop=(rfut-s*FUT_STOP_PTS) if rfut else None,
                            spot_target=(rspot+s*FUT_TARGET_PTS) if rspot else None, spot_stop=(rspot-s*FUT_STOP_PTS) if rspot else None),
                  sr_favorable=srf, sr_adverse=sra, sr_basis="nearest support/resistance from the dashboard's own level finder (spot)",
                  selected_plan=sel, spread_rules=dict(TARGET_PCT=TARGET_PCT, BUFFER_LOSS_X=BUFFER_LOSS_X, BUFFER_SHORT_DELTA=BUFFER_SHORT_DELTA, CALL_TARGET_PCT=CALL_TARGET_PCT,
                  CALL_BUFFER_LOSS_X=CALL_BUFFER_LOSS_X, NK_TARGET_PTS=NK_TARGET_PTS, NK_STOP_PTS=NK_STOP_PTS))
    poss = [dict(pid=p_.get("pid"), status=p_.get("status"), side=p_.get("side"), origin=p_.get("origin"), trade_id=p_.get("trade_id"), kind=p_.get("kind", "SPREAD"))
            for p_ in list(POSITIONS) if not p_.get("idle") and p_.get("status") in ("OPEN", "WAITING")]
    prevs = [e for e in SR["recent"] if e[1] == key and e[2] == st and e[0] < t0]
    if onset != "CLEAN" and onset != "SESSION_OPEN": flags.append("ONSET_TIME_UNCERTAIN")
    num = name.split(".")[0] if name[:1].isdigit() else ("0" if key == "verdict" else "")
    row = dict(schema_version=SR_SCHEMA, event_id=eid, trading_day=_ist_day(t0), signal_key=key, signal_number=num, signal_name=name, enabled_in_verdict=en,
               signal_instrument=SR_META.get(key, ("", ""))[0], signal_timeframe=SR_META.get(key, ("", ""))[1], direction=st, direction_sign=s, prev_state=prev or "",
               trigger_type=ttype, onset_quality=onset, signal_eval_ts=_sr_r(t0, 3), signal_eval_time_ist=_sr_tist(t0), detected_ts=_sr_r(wall, 3), detected_time_ist=_sr_tist(wall),
               detection_lag_s=_sr_r(wall-t0, 3), minutes_since_open=_sr_r((_sr_tod(t0)-33300)/60, 2), process_uptime_s=_sr_r(wall-SR_T0, 0),
               ref_spot=rspot, ref_spot_tick_age_s=_sr_r(wall-iq["t"], 2) if iq.get("t") else None, ref_fut=rfut, ref_fut_tick_age_s=_sr_r(wall-fq["t"], 2) if fq.get("t") else None,
               fut_key=FUT_KEY, fut_basis=_sr_r(rfut-rspot, 2) if (rfut and rspot) else None, trigger_detail=det, rule_text=rule, strength_label=lab, strength_value=sval, strength_basis=sbasis,
               thresholds_json=_sr_j(thr), indicator_values_json=_sr_j(_sr_locals(loc)), snapshot_status=snap, snapshot_eval_ts=_sr_r(sts, 3), verdict_state=v, verdict_detail=vd,
               combine_mode=SIG_COMBINE, n_enabled=sum(1 for k in pool if cur[k][2]), n_agree_enabled=len(ag_en), n_oppose_enabled=len(op_en), n_agree_all=len(ag_all), n_oppose_all=len(op_all),
               agreeing_signals=";".join(ag_all), opposing_signals=";".join(op_all), other_signals_json=_sr_j(others),
               day_open=ctx.get("day_open"), day_high=ctx.get("day_high"), day_low=ctx.get("day_low"), prev_close=ctx.get("prev_close"), day_change_pct=_sr_r(ctx.get("day_change_pct"), 3),
               atr5_pts=_sr_r(ctx.get("atr5_pts"), 2), vwap=_sr_r(ctx.get("vwap"), 2), vwap_sigma=_sr_r(ctx.get("vwap_sigma"), 2), sr_resistance_1=_sr_r(res1, 2), sr_support_1=_sr_r(sup1, 2),
               levels_json=_sr_j(levels), market_context_json=_sr_j(ctx), open_positions_json=_sr_j(poss), auto_trade_enabled=SIG_AUTO_TRADE,
               auto_trade_blockers=" | ".join(_sr_blockers(nw, wall, key, st, v)), seconds_since_prev_same_event=_sr_r(t0-prevs[-1][0], 1) if prevs else None,
               recent_candles_json=_sr_j(rc), missing_data_flags=";".join(dict.fromkeys(flags)))
    _sr_append(SR_EVENTS, SR_EV_COLS, row); SR["ids_ev"].add(eid)
    ev = _sr_new_track(eid, key, name, st, t0, rspot, rfut, FUT_KEY, onset, levels); ev["verdict0"] = v
    SR["active"][eid] = ev; SR["open"][key] = dict(id=eid, t0=t0, dir=st); SR["recent"].append([t0, key, st, eid]); SR["dirty"] = True
    _sr_chg(wall, "EVENT_OPENED", signal_key=key, signal_name=name, event_id=eid, prev_state=prev or "", new_state=st, new_strength=lab, reason_detail=det,
            seconds_since_event=_sr_r(wall-t0, 3), spot=rspot, fut=rfut, enabled_in_verdict=en, n_agree_enabled=len(ag_en), n_oppose_enabled=len(op_en), verdict_state=v,
            all_states=";".join(f"{k}={x[0]}" for k, x in cur.items()), extra_json=_sr_j(dict(trigger_type=ttype, onset_quality=onset)))
    _sr_save(force=True)


# ---------- live tracking and horizon finalization
def _sr_sample(ev, wall, qs, cur, v):
    pool = [k for k in cur if k not in (ev["key"], "verdict")]; od = _sr_opp(ev["dir"])
    oe = sum(1 for k in pool if cur[k][0] == od and cur[k][2]); oa = sum(1 for k in pool if cur[k][0] == od)
    if wall <= ev["t0"]+max(SR_HORIZONS)*60: ev["opp"] = [max(ev["opp"][0], oe), max(ev["opp"][1], oa)]
    s = _sr_sign(ev["dir"])
    for inst, L in ev["lv"].items():
        q = qs.get(UNDERLYING_KEY if inst == "SPOT" else ev["fk"]) or {}
        px, tk = q.get("ltp"), q.get("t"); fresh = px is not None and tk is not None and wall-tk <= SR_STALE_QUOTE
        for H in SR_HORIZONS:
            hk = f"{H}|{inst}"; tgt = ev["t0"]+H*60
            if hk in ev["snap"] or wall < tgt: continue
            gap = max(L["gap"], (tgt-L["last"]) if L["last"] else 1e9)
            sn = dict(fmax=L["fmax"], fmin=L["fmin"], tmax=L["tmax"], tmin=L["tmin"], n=L["n"], gap=gap, tt=L["tt"], ts=L["ts"], tsf=L["tsf"], tsa=L["tsa"],
                      intr=ev.get("intr", False), opp=list(ev["opp"]), verdict=v, sig=cur.get(ev["key"], ("",))[0])
            if fresh and wall-tgt <= SR_LIVE_MAX_LAG: sn.update(obs=px, otk=tk, err=max(0.0, tk-tgt))
            ev["snap"][hk] = sn; SR["dirty"] = True
        if fresh and wall <= ev["t0"]+max(SR_HORIZONS)*60:
            f = (px-L["ref"])*s; dt = wall-ev["t0"]
            if L["last"] is not None: L["gap"] = max(L["gap"], wall-L["last"])
            elif dt > SR_LIVE_MAX_GAP: L["gap"] = max(L["gap"], dt)
            L["last"] = wall; L["n"] += 1
            if f > L["fmax"]: L["fmax"], L["tmax"] = f, dt
            if f < L["fmin"]: L["fmin"], L["tmin"] = f, dt
            if ev.get("T") and L["tt"] is None and f >= ev["T"]: L["tt"] = dt
            if ev.get("S") and L["ts"] is None and f <= -ev["S"]: L["ts"] = dt
            if inst == "SPOT":
                if ev.get("srf") and L["tsf"] is None and (px-ev["srf"])*s >= 0: L["tsf"] = dt
                if ev.get("sra") and L["tsa"] is None and (px-ev["sra"])*s <= 0: L["tsa"] = dt

def _sr_candles(ev, inst, cache):
    k = UNDERLYING_KEY if inst == "SPOT" else ev["fk"]
    if k not in cache:
        with clock: cache[k] = dict(CANDLES.get(k, {})) if k else {}
    return cache[k]

def _sr_finalize(ev, wall, cache):
    s = _sr_sign(ev["dir"])
    for H in SR_HORIZONS:
        for inst in SR_INSTR:
            hk = f"{H}|{inst}"; tgt = ev["t0"]+H*60
            if hk in ev["done"] or wall < tgt: continue
            oid = f"{ev['id']}|{H}|{inst}"
            base = dict(schema_version=SR_SCHEMA, outcome_id=oid, event_id=ev["id"], trading_day=ev["day"], signal_key=ev["key"], signal_name=ev["name"], direction=ev["dir"],
                        onset_quality=ev.get("onset", ""), horizon_min=H, instrument=inst, finalized_ts=_sr_r(wall, 3), finalized_time_ist=_sr_tist(wall),
                        signal_eval_ts=_sr_r(ev["t0"], 3), target_ts=_sr_r(tgt, 3), target_time_ist=_sr_tist(tgt), flat_threshold_pts=SC_MIN_MOVE,
                        rule_target_pts=ev.get("T"), rule_stop_pts=ev.get("S"))
            L = ev["lv"].get(inst); flags = []
            if oid in SR["ids_out"]: ev["done"].append(hk); continue
            if L is None:
                row = dict(base, status="UNRESOLVED_NO_REFERENCE", missing_data_flags="NO_REFERENCE_PRICE_AT_SIGNAL")
            elif _ist_day(tgt) != ev["day"] or _sr_tod(tgt) >= SR_CLOSE_TOD:
                row = dict(base, ref_price=L["ref"], status="NOT_REACHED_MARKET_CLOSED", missing_data_flags="HORIZON_AFTER_15:30")
            else:
                sn = ev["snap"].get(hk) or {}; d = None; obs = otk = err = None; meth = ""
                if sn.get("obs") is not None: obs, otk, err, meth = sn["obs"], sn["otk"], sn["err"], "LIVE_TICK"
                elif wall >= tgt+5:
                    d = _sr_candles(ev, inst, cache); m = int(tgt//60*60)-60
                    if m in d: obs, otk, err, meth = d[m][3], m+60, -(tgt-(m+60)), "CANDLE_CLOSE_ASOF"; flags.append("OBS_FROM_1M_CANDLE")
                if obs is None:
                    if wall < tgt+SR_GIVE_UP: continue
                    row = dict(base, ref_price=L["ref"], status="UNRESOLVED_NO_DATA", missing_data_flags="NO_PRICE_AT_HORIZON")
                else:
                    ref = L["ref"]; mv = obs-ref; smv = mv*s
                    res = "WIN" if smv >= SC_MIN_MOVE else "LOSS" if smv <= -SC_MIN_MOVE else "FLAT"
                    live_ok = bool(sn) and not sn.get("intr") and sn.get("n", 0) > 0 and sn.get("gap", 1e9) <= SR_LIVE_MAX_GAP
                    if sn and not live_ok: flags.append("LIVE_TRACKING_GAP_OR_RESTART")
                    if not sn: flags.append("NO_LIVE_SAMPLES")
                    # candle excursions: only 1-min bars lying fully inside (signal, horizon]
                    if d is None: d = _sr_candles(ev, inst, cache)
                    m0 = int(math.ceil(ev["t0"]/60)*60); bars_exp = list(range(m0, int(tgt//60*60), 60)); bars = [(m, d[m]) for m in bars_exp if m in d]
                    cmx = cmn = None; c_first = "NONE"
                    if bars:
                        favs = [((c[1]-ref)*s, (c[2]-ref)*s) for _, c in bars]
                        hi_f = [max(a, b) for a, b in favs]; lo_f = [min(a, b) for a, b in favs]
                        cmx, cmn = max(0.0, max(hi_f)), max(0.0, -min(lo_f))
                        for a_, b_ in zip(hi_f, lo_f):
                            hitT = ev.get("T") and a_ >= ev["T"]; hitS = ev.get("S") and b_ <= -ev["S"]
                            if hitT and hitS: c_first = "UNKNOWN_SAME_BAR"; break
                            if hitT: c_first = "TARGET"; break
                            if hitS: c_first = "STOP"; break
                    if len(bars) < len(bars_exp): flags.append("CANDLE_BARS_MISSING")
                    if live_ok:
                        lo_ = min(sn["fmin"], smv) if meth == "LIVE_TICK" else sn["fmin"]; hi_ = max(sn["fmax"], smv) if meth == "LIVE_TICK" else sn["fmax"]
                        mfe, mae, xm = max(0.0, hi_), max(0.0, -lo_), "LIVE_SAMPLES"
                        tT, tS = sn.get("tt"), sn.get("ts")
                        tT = tT if (tT is not None and tT <= H*60) else None; tS = tS if (tS is not None and tS <= H*60) else None
                        ft = "NONE" if tT is None and tS is None else "TARGET" if (tS is None or (tT is not None and tT <= tS)) else "STOP"
                        tgt_t, stp_t = tT is not None, tS is not None
                        srf_t = sn.get("tsf") is not None and sn["tsf"] <= H*60 if ev.get("srf") else None
                        sra_t = sn.get("tsa") is not None and sn["tsa"] <= H*60 if ev.get("sra") else None
                    elif bars and len(bars) == len(bars_exp):
                        mfe, mae, xm = cmx, cmn, "CANDLE_1M"; tT = tS = None
                        tgt_t = bool(ev.get("T")) and cmx >= ev["T"]; stp_t = bool(ev.get("S")) and cmn >= ev["S"]; ft = c_first
                        srf_t = (cmx >= abs(ev["srf"]-ref)) if (ev.get("srf") and inst == "SPOT") else None
                        sra_t = (cmn >= abs(ref-ev["sra"])) if (ev.get("sra") and inst == "SPOT") else None
                        flags.append("EXCURSION_FROM_CANDLES_EXCLUDES_PARTIAL_EDGE_MINUTES")
                    else:
                        mfe = mae = None; xm = "UNAVAILABLE"; tT = tS = None; tgt_t = stp_t = srf_t = sra_t = None; ft = "UNKNOWN"; flags.append("EXCURSION_UNAVAILABLE")
                    if inst != "SPOT": srf_t = sra_t = None
                    # how the signal itself evolved inside the window (from the state-change log of this event)
                    chg = [c for c in ev["chg"] if c[0] <= tgt]
                    st_h = chg[-1][1] if chg else ev["dir"]
                    rev = next((c[0] for c in chg if c[1] == _sr_opp(ev["dir"])), None); inv = next((c[0] for c in chg if c[1] != ev["dir"]), None)
                    if ev.get("intr"): flags.append("EVOLUTION_MAY_MISS_CHANGES_DURING_DOWNTIME")
                    oth = [e for e in SR["recent"] if ev["t0"] < e[0] <= tgt and e[1] != ev["key"]]
                    row = dict(base, status="FINAL", ref_price=ref, obs_price=obs, obs_method=meth, obs_tick_ts=_sr_r(otk, 3), obs_time_error_s=_sr_r(err, 2),
                               move_pts=_sr_r(mv, 2), move_pct=_sr_r(mv/ref*100, 4), signed_move_pts=_sr_r(smv, 2), signed_move_pct=_sr_r(smv/ref*100, 4), result=res,
                               raw_direction_correct=(None if mv == 0 else smv > 0), mfe_pts=_sr_r(mfe, 2), mae_pts=_sr_r(mae, 2), excursion_method=xm,
                               live_mfe_pts=_sr_r(max(0.0, sn["fmax"]), 2) if sn else None, live_mae_pts=_sr_r(max(0.0, -sn["fmin"]), 2) if sn else None,
                               live_t_mfe_s=_sr_r(sn.get("tmax"), 1) if sn else None, live_t_mae_s=_sr_r(sn.get("tmin"), 1) if sn else None,
                               live_samples=sn.get("n") if sn else 0, live_max_gap_s=_sr_r(sn.get("gap"), 1) if sn else None, tracking_interrupted=bool(sn.get("intr") or ev.get("intr")),
                               candle_mfe_pts=_sr_r(cmx, 2), candle_mae_pts=_sr_r(cmn, 2), candle_bars_used=len(bars), candle_bars_expected=len(bars_exp),
                               target_touched=tgt_t, stop_touched=stp_t, first_touch=ft, t_target_s=_sr_r(tT, 1), t_stop_s=_sr_r(tS, 1),
                               sr_favorable_level=_sr_r(ev.get("srf"), 2) if inst == "SPOT" else None, sr_favorable_touched=srf_t,
                               sr_adverse_level=_sr_r(ev.get("sra"), 2) if inst == "SPOT" else None, sr_adverse_touched=sra_t,
                               signal_changes_in_window=len(chg), signal_state_at_horizon=st_h, reversed_in_window=rev is not None, t_reversal_s=_sr_r(rev-ev["t0"], 1) if rev else None,
                               invalidated_in_window=inv is not None, t_invalidated_s=_sr_r(inv-ev["t0"], 1) if inv else None,
                               verdict_at_horizon=sn.get("verdict", "") if sn else "", max_opposing_enabled=(sn.get("opp") or [None, None])[0] if sn else None,
                               max_opposing_all=(sn.get("opp") or [None, None])[1] if sn else None,
                               other_events_same_dir=sum(1 for e in oth if e[2] == ev["dir"]), other_events_opp_dir=sum(1 for e in oth if e[2] != ev["dir"]),
                               other_event_keys=";".join(f"{e[1]}:{e[2]}" for e in oth), missing_data_flags=";".join(dict.fromkeys(flags)))
            _sr_append(SR_OUTCOMES, SR_OUT_COLS, row); SR["ids_out"].add(oid); ev["done"].append(hk); SR["dirty"] = True
            _sr_chg(wall, "HORIZON_FINALIZED", signal_key=ev["key"], signal_name=ev["name"], event_id=ev["id"], new_state=row.get("status"),
                    reason_detail=f"{H}m {inst}: {row.get('result', row.get('status'))} signed move {row.get('signed_move_pts', '')}", seconds_since_event=_sr_r(wall-ev["t0"], 1),
                    extra_json=_sr_j(dict(horizon=H, instrument=inst, obs_method=row.get("obs_method"), excursion_method=row.get("excursion_method"))))


# ---------- trades: which signal events were active when a trade opened, and how the trade ended
def _sr_trades(nw, wall, cur):
    T = SR["trades"]
    for p in list(POSITIONS):
        tid = p.get("trade_id")
        if not tid or p.get("idle") or p.get("status") not in ("OPEN", "CLOSED") or tid in T: continue
        ets = p.get("entry_ts") or wall; tdir = "BULL" if p.get("side") == "PUT" else "BEAR"; org = p.get("origin", "")
        links = []
        for key, o in SR["open"].items():
            if o["t0"] > ets+2: continue
            if o["dir"] == tdir:
                role = "DIRECT_CAUSE" if (key == "verdict" and org == "signal") else ("CONTRIBUTING_AGREE" if org == "signal" else "CONCURRENT_AGREE")
            else: role = "ACTIVE_OPPOSED_AT_ENTRY"
            links.append([o["id"], key, role])
        late = wall-ets > 10
        T[tid] = dict(entry_ts=ets, side=p.get("side"), dir=tdir, origin=org, kind=p.get("kind", "SPREAD"), opt=p.get("opt"), links=links, closed=False, miss=0, late=late, pid=p.get("pid"))
        ex = dict(entry_time=p.get("entry_time"), entry_spot=p.get("entry_spot"), credit=p.get("credit"), qty=p.get("qty"), short=(p.get("short") or {}).get("strike"),
                  long=(p.get("long") or {}).get("strike"), label=p.get("ilabel"), registered_late=late, note=("registered after a restart: links may be incomplete" if late else ""))
        _sr_chg(wall, "TRADE_OPENED", change_id=f"TO-{tid}", trade_id=tid, trade_side=p.get("side"), trade_origin=org, reason_detail=f"{len(links)} signal event(s) active at entry",
                affected_events_json=_sr_j(links), extra_json=_sr_j(ex))
        for eid, key, role in links:
            _sr_chg(wall, "TRADE_ENTRY_LINK", change_id=f"TL-{tid}-{eid}", signal_key=key, event_id=eid, trade_id=tid, trade_role=role, trade_side=p.get("side"), trade_origin=org,
                    seconds_since_event=_sr_r(ets-SR["open"][key]["t0"], 1), reason_detail="association: signal was in this state when the trade was entered")
        SR["dirty"] = True
    for tid, tr in T.items():
        if tr.get("closed"): continue
        p = next((p_ for p_ in POSITIONS if p_.get("trade_id") == tid), None)
        if p is not None and p.get("status") != "CLOSED": tr["miss"] = 0; continue
        net = gross = None; reason = ""; src = "position"
        if p is not None:
            exd = p.get("exit") or {}; net, gross, reason = exd.get("net"), exd.get("gross"), exd.get("reason", "")
        else:
            tr["miss"] = tr.get("miss", 0)+1
            if tr["miss"] < 30: continue
            src = "trades.csv"
        part = 0.0; npart = 0; found = False
        try:
            for r in _sr_read(LEDGER):
                t_ = r.get("trade_id", "")
                if t_.startswith(tid+"-P"): part += float(r.get("net_pnl") or 0); npart += 1
                elif t_ == tid and p is None: found = True; net = float(r.get("net_pnl") or 0); gross = _sr_f(r.get("gross_pnl")); reason = r.get("exit_reason", "")
        except Exception: pass
        if p is None and not found:
            tr["closed"] = True; _sr_chg(wall, "TRADE_UNTRACKED", change_id=f"TU-{tid}", trade_id=tid, reason_detail="trade disappeared without a ledger row (e.g. 'Clear all'): outcome unknown")
            continue
        tot = (net or 0.0)+part; tr.update(closed=True, net=tot, reason=reason)
        ex = dict(final_leg_net=net, partial_exits=npart, partial_net=part, source=src, exit=(p.get("exit") if p is not None else None))
        _sr_chg(wall, "TRADE_CLOSED", change_id=f"TC-{tid}", trade_id=tid, trade_side=tr["side"], trade_origin=tr["origin"], trade_gross_pnl=_sr_r(gross, 2), trade_net_pnl=_sr_r(tot, 2),
                trade_exit_reason=reason, affected_events_json=_sr_j(tr["links"]), extra_json=_sr_j(ex))
        for eid, key, role in tr["links"]:
            _sr_chg(wall, "TRADE_CLOSED_LINK", change_id=f"TCL-{tid}-{eid}", signal_key=key, event_id=eid, trade_id=tid, trade_role=role, trade_side=tr["side"], trade_origin=tr["origin"],
                    trade_gross_pnl=_sr_r(gross, 2), trade_net_pnl=_sr_r(tot, 2), trade_exit_reason=reason)
        if str(reason).startswith("SIGNAL:"):
            o = SR["open"].get("verdict")
            if o and o["dir"] != tr["dir"]:
                _sr_chg(wall, "TRADE_EXIT_BY_SIGNAL", change_id=f"TX-{tid}-{o['id']}", signal_key="verdict", event_id=o["id"], trade_id=tid, trade_role="EXIT_TRIGGER",
                        trade_side=tr["side"], trade_origin=tr["origin"], trade_net_pnl=_sr_r(tot, 2), trade_exit_reason=reason)
        SR["dirty"] = True


# ---------- the hook called once per engine cycle at the end of run_signals()
def _sr_hook(nw, wall, qs, items, v, vd):
    if not SIGNAL_RESEARCH_ON: return
    with SR_LOCK:
        if not SR["loaded"]: _sr_load(wall)
        mo = market_open(nw); today = _ist_day(wall)
        cur = {i["key"]: (i["state"], i["detail"], bool(i["enabled"]), i["name"], i.get("rule", "")) for i in items}
        cur["verdict"] = (v, vd, True, "COMBINED VERDICT", f"SIG_COMBINE={SIG_COMBINE}: all = every ticked signal agrees | any = one signal and none opposing | majority")
        first = SR["first"]; SR["first"] = False
        opening = SR["mo_prev"] is False and mo
        if opening:
            for key, o in list(SR["open"].items()):
                if _ist_day(o["t0"]) != today:
                    _sr_chg(wall, "EVENT_ENDED", signal_key=key, event_id=o["id"], prev_state=o["dir"], new_state=o["dir"], reason_detail="previous session ended; a new session opened",
                            seconds_since_event=_sr_r(wall-o["t0"], 1), extra_json=_sr_j(dict(session_rollover=True)))
                    SR["open"].pop(key, None)
        news = []
        for key, (st, det, en, name, rule) in cur.items():
            lab = ("STRONG" if "STRONG" in (det or "") else "NORMAL") if st in SR_BB else ""
            pv = SR["last"].get(key); pst, plab = (pv[0], pv[1]) if pv else (None, None)
            if pst is not None and pst != st:
                o = SR["open"].get(key); act = [e["id"] for e in SR["active"].values() if e["key"] == key]
                for e in SR["active"].values():
                    if e["key"] == key: e["chg"].append([wall, st]); del e["chg"][:-200]
                if mo or act or (o is not None):
                    sse = (wall-o["t0"]) if o else None
                    _sr_chg(wall, "STATE_CHANGE", signal_key=key, signal_name=name, event_id=(o or {}).get("id", ""), prev_state=pst, new_state=st, prev_strength=plab, new_strength=lab,
                            reason_detail=det, seconds_since_event=_sr_r(sse, 1), spot=(qs.get(UNDERLYING_KEY) or {}).get("ltp"), fut=(qs.get(FUT_KEY) or {}).get("ltp"),
                            enabled_in_verdict=en, verdict_state=v, all_states=";".join(f"{k}={x[0]}" for k, x in cur.items()), affected_events_json=_sr_j(act),
                            extra_json=_sr_j(dict(market_open=mo, first_check_after_start=first)))
                if o is not None and o["dir"] != st:
                    _sr_chg(wall, "EVENT_ENDED", signal_key=key, signal_name=name, event_id=o["id"], prev_state=o["dir"], new_state=st, reason_detail=det,
                            seconds_since_event=_sr_r(wall-o["t0"], 1), extra_json=_sr_j(dict(duration_s=_sr_r(wall-o["t0"], 1), possibly_restart_artifact=first or (wall-SR_T0 < 900 and st == "NA"))))
                    if o["id"] in SR["active"]: SR["active"][o["id"]]["end"] = wall
                    SR["open"].pop(key, None)
            elif pst == st and st in SR_BB and plab != lab and mo:
                o = SR["open"].get(key)
                _sr_chg(wall, "STRENGTH_CHANGE", signal_key=key, signal_name=name, event_id=(o or {}).get("id", ""), prev_state=pst, new_state=st, prev_strength=plab, new_strength=lab,
                        reason_detail=det, seconds_since_event=_sr_r(wall-o["t0"], 1) if o else None, verdict_state=v)
            if mo and st in SR_BB:
                if pst is None: news.append((key, "FIRST_OBSERVATION", "STARTUP_UNKNOWN_ONSET", pst))
                elif pst != st:
                    q = "CHANGED_WHILE_OFFLINE" if first else ("STARTUP_WARMUP" if (pst == "NA" and wall-SR_T0 < 900) else "CLEAN")
                    news.append((key, "FLIP" if pst in SR_BB else "STATE_CHANGE", q, pst))
                elif opening and SR["open"].get(key) is None: news.append((key, "ACTIVE_AT_OPEN", "SESSION_OPEN", pst))
            SR["last"][key] = [st, lab, wall, today]
        SR["mo_prev"] = mo
        for key, ttype, q, pst in news:
            st, det, en, name, rule = cur[key]
            try: _sr_event(nw, wall, qs, key, st, det, en, name, pst, ttype, q, cur, v, vd, rule)
            except Exception as e: _sr_err("event "+key, e)
        cache = {}
        for eid, ev in list(SR["active"].items()):
            try:
                _sr_sample(ev, wall, qs, cur, v); _sr_finalize(ev, wall, cache)
                if len(ev["done"]) >= len(SR_HORIZONS)*len(SR_INSTR):
                    SR["active"].pop(eid, None); SR["dirty"] = True
                    _sr_chg(wall, "TRACKING_COMPLETE", signal_key=ev["key"], signal_name=ev["name"], event_id=eid, seconds_since_event=_sr_r(wall-ev["t0"], 1),
                            reason_detail="all horizons finalized")
            except Exception as e: _sr_err("track "+eid, e)
        try: _sr_trades(nw, wall, cur)
        except Exception as e: _sr_err("trades", e)
        SR["recent"] = [e for e in SR["recent"] if wall-e[0] < 86400]
        if SR["dirty"] and wall-SR["saved"] >= 10: _sr_save()
    if wall-SR["rep_t"] >= SR_REPORT_EVERY: SR["rep_t"] = wall; _sr_report_async()

def _sr_safe_hook(nw, wall, qs, items, v, vd):
    try: _sr_hook(nw, wall, qs, items, v, vd)
    except Exception as e: _sr_err("hook", e)

def _sr_shutdown():
    try:
        with SR_LOCK:
            if SR["loaded"]: _sr_chg(time.time(), "TRACKER_STOP", reason_detail=f"engine stopped; pending_events={len(SR['active'])}"); _sr_save(force=True)
        if SR["loaded"]: _sr_write_report()
    except Exception as e: _sr_err("shutdown", e)


# ---------- statistics / daily report
def _sr_med(x): x = sorted(x); n = len(x); return None if not n else (x[n//2] if n % 2 else (x[n//2-1]+x[n//2])/2)
def _sr_avg(x): return sum(x)/len(x) if x else None
SR_MIN_N = 30
def _sr_wilson(k, n, z=1.96):
    if n < SR_MIN_N: return None
    p = k/n; d = 1+z*z/n; c = (p+z*z/(2*n))/d; h = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [round((c-h)*100, 1), round((c+h)*100, 1)]
def _sr_stats(rows):
    fin = [r for r in rows if r.get("status") == "FINAL"]
    num = lambda k: [float(r[k]) for r in fin if r.get(k) not in (None, "")]
    w = sum(r["result"] == "WIN" for r in fin); l = sum(r["result"] == "LOSS" for r in fin); fl = sum(r["result"] == "FLAT" for r in fin); dec = w+l
    rc = [r for r in fin if r.get("raw_direction_correct") in ("1", "0")]
    rv = [r for r in fin if r.get("reversed_in_window") in ("1", "0")]
    tk = [r for r in fin if r.get("target_touched") in ("1", "0")]; sk = [r for r in fin if r.get("stop_touched") in ("1", "0")]
    sm, sp, mfe, mae = num("signed_move_pts"), num("signed_move_pct"), num("mfe_pts"), num("mae_pts")
    out = dict(n_rows=len(rows), n_final=len(fin), n_unresolved_no_data=sum(r.get("status") == "UNRESOLVED_NO_DATA" for r in rows),
               n_not_reached_market_closed=sum(r.get("status") == "NOT_REACHED_MARKET_CLOSED" for r in rows), n_no_reference=sum(r.get("status") == "UNRESOLVED_NO_REFERENCE" for r in rows),
               wins=w, losses=l, flats=fl, n_decisive=dec, directional_accuracy_pct=_sr_r(w/dec*100, 1) if dec else None,
               accuracy_95ci_wilson_pct=_sr_wilson(w, dec), positive_move_rate_pct=_sr_r(sum(r["raw_direction_correct"] == "1" for r in rc)/len(rc)*100, 1) if rc else None,
               avg_signed_move_pts=_sr_r(_sr_avg(sm), 2), median_signed_move_pts=_sr_r(_sr_med(sm), 2), avg_signed_move_pct=_sr_r(_sr_avg(sp), 4), median_signed_move_pct=_sr_r(_sr_med(sp), 4),
               avg_mfe_pts=_sr_r(_sr_avg(mfe), 2), median_mfe_pts=_sr_r(_sr_med(mfe), 2), avg_mae_pts=_sr_r(_sr_avg(mae), 2), median_mae_pts=_sr_r(_sr_med(mae), 2), n_excursion=len(mfe),
               reversal_rate_pct=_sr_r(sum(r["reversed_in_window"] == "1" for r in rv)/len(rv)*100, 1) if rv else None, n_reversal_known=len(rv),
               target_touch_rate_pct=_sr_r(sum(r["target_touched"] == "1" for r in tk)/len(tk)*100, 1) if tk else None,
               stop_touch_rate_pct=_sr_r(sum(r["stop_touched"] == "1" for r in sk)/len(sk)*100, 1) if sk else None,
               target_first=sum(r.get("first_touch") == "TARGET" for r in fin), stop_first=sum(r.get("first_touch") == "STOP" for r in fin))
    out["sample_note"] = ("NO DATA" if not dec else f"INSUFFICIENT SAMPLE: {dec} decisive outcome(s) < {SR_MIN_N}; figures are descriptive only, no statistical confidence is claimed"
                          if dec < SR_MIN_N else "Wilson 95% interval assumes independent outcomes; overlapping or clustered signals violate this, so treat it as optimistic")
    return out
def _sr_mini(rows):
    w = sum(r.get("result") == "WIN" for r in rows); l = sum(r.get("result") == "LOSS" for r in rows); sm = [float(r["signed_move_pts"]) for r in rows if r.get("signed_move_pts") not in (None, "")]
    return dict(n=len(rows), wins=w, losses=l, flats=sum(r.get("result") == "FLAT" for r in rows), directional_accuracy_pct=_sr_r(w/(w+l)*100, 1) if w+l else None,
                avg_signed_move_pts=_sr_r(_sr_avg(sm), 2), small_sample=(w+l) < SR_MIN_N)

SR_PRIMARY = ("CLEAN", "SESSION_OPEN")
def _sr_write_report():
    evs, outs, chg = _sr_read(SR_EVENTS), _sr_read(SR_OUTCOMES), _sr_read(SR_CHANGES)      # no lock: a half-written last row is simply ignored
    evs = [e for e in evs if e.get("event_id")]; byid = {e["event_id"]: e for e in evs}
    prim = {e["event_id"] for e in evs if e.get("onset_quality") in SR_PRIMARY}
    outs = [o for o in outs if o.get("event_id") in byid]
    keys = [x[0] for x in SIGDEF]+["verdict"]; names = {x[0]: x[1] for x in SIGDEF}; names["verdict"] = "COMBINED VERDICT"
    links = [c for c in chg if c.get("change_type") == "TRADE_ENTRY_LINK"]; closes = [c for c in chg if c.get("change_type") == "TRADE_CLOSED_LINK"]
    traded = {c["event_id"] for c in links if c.get("trade_role") in ("DIRECT_CAUSE", "CONTRIBUTING_AGREE")}
    def tod_bucket(e):
        m = _sr_f(e.get("minutes_since_open"))
        return "unknown" if m is None else "09:15-10:30" if m < 75 else "10:30-13:00" if m < 225 else "13:00-15:30"
    def section(evset, outset, with_extras):
        res = {}
        for k in keys:
            ek = [e for e in evset if e["signal_key"] == k]; ids = {e["event_id"] for e in ek if e["event_id"] in prim}
            oq = collections.Counter(e.get("onset_quality", "") for e in ek)
            r = dict(name=names.get(k, k), events_total=len(ek), events_by_onset_quality=dict(oq), events_used_for_stats=len(ids),
                     bull=sum(e["direction"] == "BULL" for e in ek if e["event_id"] in ids), bear=sum(e["direction"] == "BEAR" for e in ek if e["event_id"] in ids),
                     events_linked_to_trade=len(ids & traded), events_without_trade=len(ids-traded), horizons={})
            for inst in SR_INSTR:
                r["horizons"][inst] = {str(H): _sr_stats([o for o in outset if o["event_id"] in ids and o["instrument"] == inst and o["horizon_min"] == str(H)]) for H in SR_HORIZONS}
            if with_extras:
                base = [o for o in outset if o["event_id"] in ids and o["instrument"] == "SPOT" and o["horizon_min"] == "15" and o.get("status") == "FINAL"]
                grp = lambda f: {str(g): _sr_mini([o for o in base if f(byid[o["event_id"]]) == g]) for g in sorted({f(byid[o["event_id"]]) for o in base}, key=str)}
                r["conditions_15m_spot"] = dict(
                    note="Breakdowns of the 15-minute spot outcome by conditions present at the trigger. Small groups are flagged; they are hints for investigation, not findings.",
                    by_enabled_in_verdict=grp(lambda e: e.get("enabled_in_verdict")), by_strength_label=grp(lambda e: e.get("strength_label")),
                    by_time_of_day=grp(tod_bucket), by_direction=grp(lambda e: e.get("direction")),
                    by_n_agree_enabled=grp(lambda e: (lambda n: "3+" if n >= 3 else str(n))(int(e.get("n_agree_enabled") or 0))),
                    by_n_oppose_enabled=grp(lambda e: (lambda n: "2+" if n >= 2 else str(n))(int(e.get("n_oppose_enabled") or 0))),
                    by_verdict_agrees=grp(lambda e: e.get("verdict_state") == e.get("direction")), by_trigger_type=grp(lambda e: e.get("trigger_type")),
                    by_linked_to_trade=grp(lambda e: e["event_id"] in traded))
                tr = {}
                for c in closes:
                    if c.get("signal_key") != k: continue
                    tr.setdefault(c.get("trade_role", ""), []).append(_sr_f(c.get("trade_net_pnl")) or 0.0)
                r["trade_profitability"] = dict(note="Paper trades only, net of the script's modelled charges. An association with trades open while the signal was active, NOT the "
                                                     "signal's own directional accuracy (an option spread can profit when the index moves against the signal, and vice versa).",
                                                by_role={role: dict(n=len(v), wins=sum(x > 0 for x in v), losses=sum(x < 0 for x in v), total_net=_sr_r(sum(v), 2),
                                                                    avg_net=_sr_r(_sr_avg(v), 2), median_net=_sr_r(_sr_med(v), 2), small_sample=len(v) < SR_MIN_N) for role, v in tr.items()})
            res[k] = r
        return res
    pe = [e for e in evs if e["event_id"] in prim]
    pair = {}; cof = {}
    for e in pe:
        a = e["signal_key"]; ag = set(filter(None, e.get("agreeing_signals", "").split(";"))); op = set(filter(None, e.get("opposing_signals", "").split(";")))
        for b in keys:
            if b in (a, "verdict"): continue
            x = pair.setdefault(a, {}).setdefault(b, dict(n_events=0, b_agreed=0, b_opposed=0)); x["n_events"] += 1; x["b_agreed"] += b in ag; x["b_opposed"] += b in op
    ts = sorted(((float(e["signal_eval_ts"]), e["signal_key"], e["direction"]) for e in pe), key=lambda z: z[0])
    for i, (t, a, d) in enumerate(ts):
        for t2, b, d2 in ts:
            if b == a or abs(t2-t) > 300: continue
            x = cof.setdefault(a, {}).setdefault(b, dict(same_dir_within_5min=0, opp_dir_within_5min=0)); x["same_dir_within_5min" if d2 == d else "opp_dir_within_5min"] += 1
    for a in cof:
        na = sum(1 for e in pe if e["signal_key"] == a)
        for b in cof[a]: cof[a][b]["share_of_a_events_with_b_same_dir"] = _sr_r(cof[a][b]["same_dir_within_5min"]/na*100, 1) if na else None
    dq = dict(event_flags=dict(collections.Counter(f for e in evs for f in filter(None, e.get("missing_data_flags", "").split(";")))),
              outcome_flags=dict(collections.Counter(f for o in outs for f in filter(None, o.get("missing_data_flags", "").split(";")))),
              outcome_status=dict(collections.Counter(o.get("status") for o in outs)), snapshot_status=dict(collections.Counter(e.get("snapshot_status") for e in evs)),
              obs_method=dict(collections.Counter(o.get("obs_method") for o in outs if o.get("status") == "FINAL")),
              excursion_method=dict(collections.Counter(o.get("excursion_method") for o in outs if o.get("status") == "FINAL")))
    days = {}
    for d in sorted({e["trading_day"] for e in evs}):
        de = [e for e in evs if e["trading_day"] == d]; ids = {e["event_id"] for e in de}
        days[d] = dict(events=len(de), by_signal=section(de, [o for o in outs if o["event_id"] in ids], False))
    rep = dict(schema_version=SR_SCHEMA, generated_at_ist=_sr_tist(time.time()), files=dict(events=len(evs), outcomes=len(outs), state_changes=len(chg)),
               read_first=["Directional accuracy = wins / (wins + losses) where WIN means the price moved at least flat_threshold_pts (SC_MIN_MOVE) in the signal direction by the horizon; FLAT moves are excluded.",
                           "Trade profitability is reported separately and is only an association with trades that were open while a signal was active.",
                           f"Statistics use only events whose onset time is reliable (onset_quality in {list(SR_PRIMARY)}); restart/warm-up events are counted but excluded.",
                           f"Any figure based on fewer than {SR_MIN_N} decisive outcomes is marked INSUFFICIENT SAMPLE and no confidence interval is given.",
                           "Overlapping signals are not independent: many signals fire together, so per-signal results are correlated with each other."],
               all_time=dict(by_signal=section(evs, outs, True), pairwise_at_trigger=dict(note="For events of signal A (rows), how often signal B (columns) was already in the same / opposite state at A's trigger.", data=pair),
                             co_firing=dict(note="Events of other signals with an onset within 5 minutes of A's onset (redundancy hint: a high same-direction share means B usually fires with A).", data=cof),
                             data_quality=dq),
               days=days)
    _atomic(SR_REPORT, clean(rep))

def _sr_report_async():
    if SR.get("rep_busy"): return
    SR["rep_busy"] = True
    def run():
        try: _sr_write_report()
        except Exception as e: _sr_err("report", e)
        finally: SR["rep_busy"] = False
    threading.Thread(target=run, daemon=True).start()


# ---------- README / data dictionary (rewritten only when its text changes)
SR_DOC = {
 # shared
 "schema_version": "Format version of this file's columns (currently 1).",
 "event_id": "Unique id of one signal occurrence: <trading_day>-<signal_key>-<direction>-<signal_eval_ts in ms>. Links all three CSV files.",
 "trading_day": "IST calendar date (YYYY-MM-DD) of the signal (events/outcomes) or of the row (state changes).",
 "signal_key": "Internal signal id: vwap, rvol, orb, struct, ema, oi, contrib, delta, sweep, vwband, orbv, dtrend, dma, or verdict (the combined verdict).",
 "signal_name": "Signal name exactly as shown on the dashboard.",
 "direction": "BULL or BEAR: the state the signal entered.",
 "onset_quality": "CLEAN = signal changed state while the script was already running with data | SESSION_OPEN = state already active at 09:15 and first tradable then | "
                  "STARTUP_UNKNOWN_ONSET = active at the first check after start (true onset earlier, unknown) | STARTUP_WARMUP = went NA->state within 15 min of start "
                  "(probably data warm-up) | CHANGED_WHILE_OFFLINE = changed between a restart's saved state and its first check. Statistics use CLEAN and SESSION_OPEN only.",
 "missing_data_flags": "Semicolon list of data problems affecting this row (see FLAGS below). Empty = none detected.",
 # events
 "signal_number": "Number in the dashboard name (1-13); 0 for the combined verdict.",
 "enabled_in_verdict": "1 if the signal was ticked (counted in the combined verdict) at the trigger, else 0. Unticked signals are still evaluated and logged.",
 "signal_instrument": "Data the signal itself is computed from.",
 "signal_timeframe": "Candle size / data cadence the signal uses.",
 "direction_sign": "+1 for BULL, -1 for BEAR. signed move = raw move x direction_sign.",
 "prev_state": "State before the trigger / change (BULL/BEAR/NEUTRAL/NA); blank on first observation.",
 "trigger_type": "STATE_CHANGE (from NEUTRAL/NA), FLIP (directly from the opposite direction), FIRST_OBSERVATION (first check after start), ACTIVE_AT_OPEN (already active at 09:15).",
 "signal_eval_ts": "Original signal timestamp: epoch seconds of the signal evaluation that produced the new state (for the verdict: the engine cycle). All horizons are measured from this.",
 "signal_eval_time_ist": "signal_eval_ts as IST time with milliseconds.",
 "detected_ts": "Epoch seconds when the research logger saw the new state (engine cycle, about once per second).",
 "detected_time_ist": "detected_ts as IST time.",
 "detection_lag_s": "detected_ts - signal_eval_ts. Reference prices are taken at detection, so this is the reference-price delay.",
 "minutes_since_open": "Minutes from 09:15 IST to the signal.",
 "process_uptime_s": "Seconds the script had been running at the signal (low values = warm-up risk).",
 "ref_spot": "NIFTY spot LTP at detection (blank if the spot quote was older than 15 s).",
 "ref_spot_tick_age_s": "Age of the last spot tick at detection.",
 "ref_fut": "NIFTY futures LTP at detection (blank if missing/stale).",
 "ref_fut_tick_age_s": "Age of the last futures tick at detection.",
 "fut_key": "Upstox instrument key of the futures contract in use.",
 "fut_basis": "ref_fut - ref_spot.",
 "trigger_detail": "The signal's own explanation text at the trigger (exact conditions and numbers it printed).",
 "rule_text": "The signal's rule description from the dashboard.",
 "strength_label": "STRONG if the signal's own text marks it STRONG (only signal 1 does), otherwise NORMAL.",
 "strength_value": "Research-only magnitude read from the signal's own numbers (see strength_basis). Not used by any signal or trade rule.",
 "strength_basis": "What strength_value measures.",
 "thresholds_json": "Setting values in force at the trigger: the signal's own thresholds plus common settings (combine mode, auto-trade, cooldown, SC_MIN_MOVE, futures target/stop).",
 "indicator_values_json": "Every local value the signal function computed during the evaluation that fired (e.g. VWAP, sigma, slope, gaps, volume ratio, swing points, EMAs, OI walls). "
                          "Captured at that exact evaluation, never recomputed later. Large series are reduced to their length and last items.",
 "snapshot_status": "CAPTURED_AT_EVALUATION = indicator values are from the evaluation that fired | BUILT_FROM_ENABLED_STATES = verdict | LOCALS_UNAVAILABLE / STATE_MISMATCH / NOT_CAPTURED = not available.",
 "snapshot_eval_ts": "Epoch time of the evaluation the indicator values came from.",
 "verdict_state": "Combined verdict at the trigger.",
 "verdict_detail": "Verdict explanation text (counts of bullish / bearish signals).",
 "combine_mode": "SIG_COMBINE at the trigger (all / any / majority).",
 "n_enabled": "Number of ticked signals (excluding this one).",
 "n_agree_enabled": "Ticked signals (excluding this one) already in the same direction = confirmations.",
 "n_oppose_enabled": "Ticked signals in the opposite direction = conflicts.",
 "n_agree_all": "All signals (ticked or not, excluding this one) in the same direction.",
 "n_oppose_all": "All signals in the opposite direction.",
 "agreeing_signals": "Keys of all signals in the same direction (semicolon list).",
 "opposing_signals": "Keys of all signals in the opposite direction.",
 "other_signals_json": "State, ticked flag and detail text of every other signal and the verdict at the trigger.",
 "day_open": "First 1-min spot open today.", "day_high": "Spot high so far today.", "day_low": "Spot low so far today.",
 "prev_close": "Index previous close from the feed.", "day_change_pct": "Spot % change vs previous close.",
 "atr5_pts": "Average high-low range of the last (up to) 6 completed 5-min spot candles: a volatility reference.",
 "vwap": "Futures VWAP from signal 1's latest evaluation.", "vwap_sigma": "Futures VWAP standard deviation from signal 1's latest evaluation.",
 "sr_resistance_1": "Nearest resistance above spot from the dashboard's support/resistance finder.", "sr_support_1": "Nearest support below spot.",
 "levels_json": "Reference levels: hypothetical target/stop using the existing futures-trade rule (FUT_TARGET_PTS / FUT_STOP_PTS) in the signal direction, nearest favourable/adverse "
                "S/R, the selected trade plan's strikes, and the spread target/buffer settings. Signals themselves have no target or stop.",
 "market_context_json": "Session context: weekday, minutes since open, expiry/DTE, trade mode, open trades, session P&L, quote ages, futures bid/ask, futures volume today, VWAP slope, OI walls, top S/R levels.",
 "open_positions_json": "Paper trades open or pending at the trigger.",
 "auto_trade_enabled": "SIG_AUTO_TRADE at the trigger.",
 "auto_trade_blockers": "Conditions that would stop a signal trade at the trigger (read-only copy of the auto-trade gate). Explains signals that did not produce trades.",
 "seconds_since_prev_same_event": "Seconds since the previous event of this signal in the same direction (clustering / repeat firing).",
 "recent_candles_json": "Underlying data at the trigger: last 30 1-min spot candles, last 30 1-min futures candles with volume, last 12 5-min futures candles, last 30 minutes of estimated futures delta.",
 # outcomes
 "outcome_id": "Unique id: <event_id>|<horizon_min>|<instrument>. One row per event x horizon x instrument, written once.",
 "horizon_min": "1, 5, 15 or 30 minutes after signal_eval_ts.",
 "instrument": "SPOT (NIFTY 50 index) or FUT (NIFTY futures contract in use at the signal).",
 "status": "FINAL = measured from real prices | NOT_REACHED_MARKET_CLOSED = horizon falls at/after 15:30 | UNRESOLVED_NO_DATA = no real price within 15 min after the horizon | "
           "UNRESOLVED_NO_REFERENCE = no fresh reference price at the signal. Only FINAL rows have results.",
 "finalized_ts": "Epoch time the row was written.", "finalized_time_ist": "finalized_ts in IST.",
 "target_ts": "signal_eval_ts + horizon.", "target_time_ist": "target_ts in IST.",
 "ref_price": "Reference price (ref_spot or ref_fut of the event).",
 "obs_price": "Observed price at the horizon.",
 "obs_method": "LIVE_TICK = live last-traded price seen within 5 s after the horizon | CANDLE_CLOSE_ASOF = close of the last 1-min candle ending at or before the horizon (used after restarts/gaps).",
 "obs_tick_ts": "Epoch time of the tick (LIVE_TICK) or candle end (CANDLE) behind obs_price.",
 "obs_time_error_s": "Seconds between that tick/candle end and the horizon (positive = after, negative = before).",
 "move_pts": "obs_price - ref_price.", "move_pct": "move_pts / ref_price x 100.",
 "signed_move_pts": "move_pts x direction_sign (positive = moved the way the signal predicted).", "signed_move_pct": "signed % move.",
 "flat_threshold_pts": "SC_MIN_MOVE at finalization: moves smaller than this are FLAT (same rule as the dashboard scorecard).",
 "result": "WIN (signed move >= threshold), LOSS (<= -threshold) or FLAT. Directional only, not trade profit.",
 "raw_direction_correct": "1 if signed move > 0, 0 if < 0, blank if exactly 0 (ignores the flat threshold).",
 "mfe_pts": "Maximum favourable excursion in points between signal and horizon (>= 0). From live samples when tracking was continuous, otherwise from 1-min candles.",
 "mae_pts": "Maximum adverse excursion in points (>= 0).",
 "excursion_method": "LIVE_SAMPLES, CANDLE_1M or UNAVAILABLE.",
 "live_mfe_pts": "MFE from the logger's ~1 s price samples (may miss ticks between samples).", "live_mae_pts": "MAE from live samples.",
 "live_t_mfe_s": "Seconds after the signal when the live MFE occurred.", "live_t_mae_s": "Seconds after the signal when the live MAE occurred.",
 "live_samples": "Number of live price samples in the window.", "live_max_gap_s": "Longest gap between live samples (> 10 s means live excursions are not trusted).",
 "tracking_interrupted": "1 if the script restarted while this event was being tracked.",
 "candle_mfe_pts": "MFE from 1-min candles lying fully inside the window (partial first/last minutes excluded).", "candle_mae_pts": "MAE from those candles.",
 "candle_bars_used": "1-min candles available in the window.", "candle_bars_expected": "1-min candles that should exist in the window.",
 "rule_target_pts": "FUT_TARGET_PTS at the signal (hypothetical target distance).", "rule_stop_pts": "FUT_STOP_PTS at the signal (hypothetical stop distance).",
 "target_touched": "1 if price reached ref +/- rule_target_pts in the signal direction within the horizon.", "stop_touched": "1 if price reached the hypothetical stop.",
 "first_touch": "TARGET, STOP, NONE, UNKNOWN_SAME_BAR (both inside one 1-min candle) or UNKNOWN.",
 "t_target_s": "Seconds to the first target touch (live tracking only).", "t_stop_s": "Seconds to the first stop touch (live tracking only).",
 "sr_favorable_level": "Nearest S/R in the signal direction at the trigger (spot rows only).", "sr_favorable_touched": "1 if spot reached it within the horizon.",
 "sr_adverse_level": "Nearest S/R against the signal.", "sr_adverse_touched": "1 if spot reached it within the horizon.",
 "signal_changes_in_window": "Number of state changes of this same signal between trigger and horizon.",
 "signal_state_at_horizon": "This signal's state at the horizon.",
 "reversed_in_window": "1 if this signal flipped to the opposite direction before the horizon.", "t_reversal_s": "Seconds to that flip.",
 "invalidated_in_window": "1 if this signal left its state (any change) before the horizon.", "t_invalidated_s": "Seconds to that change.",
 "verdict_at_horizon": "Combined verdict at the horizon (live tracking only).",
 "max_opposing_enabled": "Most ticked signals simultaneously opposing this event at any time in the window.", "max_opposing_all": "Same for all signals.",
 "other_events_same_dir": "New events of other signals in the same direction inside the window.", "other_events_opp_dir": "New events of other signals in the opposite direction.",
 "other_event_keys": "Those events as key:direction.",
 # state changes
 "change_id": "Unique row id (trade rows use deterministic ids so they are never duplicated across restarts).",
 "ts": "Epoch seconds of the row.", "time_ist": "IST time of the row.",
 "change_type": "STATE_CHANGE, STRENGTH_CHANGE, EVENT_OPENED, EVENT_ENDED, HORIZON_FINALIZED, TRACKING_COMPLETE, TRADE_OPENED, TRADE_ENTRY_LINK, TRADE_CLOSED, TRADE_CLOSED_LINK, "
                 "TRADE_EXIT_BY_SIGNAL, TRADE_UNTRACKED, TRACKER_START, TRACKER_STOP.",
 "new_state": "New state (for HORIZON_FINALIZED: the outcome status).",
 "prev_strength": "Previous strength label.", "new_strength": "New strength label.",
 "reason_detail": "The signal's own explanation text at the change (the reason), or a description of the event.",
 "seconds_since_event": "Seconds since the linked event's signal_eval_ts.",
 "spot": "Spot LTP at the change.", "fut": "Futures LTP at the change.",
 "all_states": "Every signal's state at that moment as key=STATE (lets you rebuild confirmations/conflicts over time).",
 "affected_events_json": "Event ids still being tracked for this signal (or the trade's linked events).",
 "trade_id": "Paper trade id from trades.csv / trade_events.csv.",
 "trade_role": "DIRECT_CAUSE (verdict event behind a signal-originated trade) | CONTRIBUTING_AGREE (signal agreed when a signal trade opened) | CONCURRENT_AGREE (agreed with a manual/auto/startup trade) | "
               "ACTIVE_OPPOSED_AT_ENTRY (signal pointed the other way) | EXIT_TRIGGER (opposite verdict that closed the trade).",
 "trade_side": "PUT = bullish trade (bull put spread / bullish single or futures), CALL = bearish.",
 "trade_origin": "signal, manual, auto (re-entry) or startup, as recorded by the engine.",
 "trade_gross_pnl": "Gross P&L of the final exit (Rs).", "trade_net_pnl": "Net P&L after the script's modelled charges, including partial exits (Rs).",
 "trade_exit_reason": "Exit reason recorded by the engine.", "extra_json": "Additional structured details for the row.",
}
for _c in SR_EV_COLS+SR_OUT_COLS+SR_CHG_COLS: SR_DOC.setdefault(_c, "(see column name)")

def _sr_readme_text():
    W = lambda cols: "\n".join(f"  {c:<34} {SR_DOC.get(c, '')}" for c in cols)
    sigs = "\n".join(f"  {k:<8} {n}  [{SR_META.get(k, ('', ''))[0]}; {SR_META.get(k, ('', ''))[1]}]" for k, n, _, _ in SIGDEF)
    return f"""SIGNAL RESEARCH LOG  (schema {SR_SCHEMA})
Written automatically by the dashboard script. Nothing here changes signals, trades or the dashboard: the logger only observes.

FILES
  signal_events.csv          one row per signal occurrence, with the full trigger snapshot (append-only)
  signal_outcomes.csv        one row per occurrence x horizon (1, 5, 15, 30 min) x instrument (SPOT, FUT), written once when real data allows (append-only)
  signal_state_changes.csv   every later change of state/strength/validity, horizon closures, and trade links/outcomes (append-only)
  daily_signal_report.json   statistics recomputed from the CSVs every {int(SR_REPORT_EVERY/60)} minutes and when the engine stops (all-time and per day)
  signal_research_README.txt this file
  _tracker_state.json        internal: events still being tracked, so tracking survives restarts. Do not edit.
  CSV rows are never edited or deleted. If a future version changes the columns, the old file is renamed *_schemaold_<time>.csv and a new one is started.
  'Clear all' in the dashboard does NOT touch this folder.

SIGNALS LOGGED
  All 13 signals defined in the script (the 12 numbered ones plus "13. DMA trend") and the combined verdict (key 'verdict'),
  whether or not they are ticked. Ticked status is recorded per event (enabled_in_verdict).
{sigs}
  verdict  COMBINED VERDICT  [{SR_META['verdict'][0]}]

WHAT COUNTS AS A SIGNAL OCCURRENCE
  A signal entering BULL or BEAR during market hours (09:15-15:30 IST): from NEUTRAL/NA, a direct flip from the opposite side,
  the first check after the script starts, or a state already active when the market opens. This is the same transition the dashboard's
  signal log uses, but without the scorecard's 15-minute de-duplication: every occurrence is kept (see seconds_since_prev_same_event).
  An event stays "open" until the signal leaves that state (EVENT_ENDED). Outcomes are tracked for 30 minutes regardless.

CALCULATION DEFINITIONS
  Original timestamp      signal_eval_ts = time of the evaluation that produced the state. Signal 1 is evaluated on every futures tick; the others every
                          SIG_RF_<NAME> seconds (default 5), so a condition may have become true up to that many seconds earlier.
  Reference price         spot / futures LTP at detection (detection_lag_s after signal_eval_ts, normally <= 1 s). Quotes older than {SR_STALE_QUOTE:.0f} s are not used.
  Observed price          live LTP seen within {SR_LIVE_MAX_LAG:.0f} s after the horizon; otherwise the close of the last 1-min candle ending at/before the horizon.
                          If neither exists within {SR_GIVE_UP/60:.0f} min, the row is UNRESOLVED_NO_DATA. Horizons at/after 15:30 are NOT_REACHED_MARKET_CLOSED. Nothing is guessed.
  Signed move             (observed - reference) x (+1 BULL / -1 BEAR), in points and %.
  Result                  WIN if signed move >= SC_MIN_MOVE (dashboard scorecard setting, default 8 pts), LOSS if <= -SC_MIN_MOVE, else FLAT.
  Directional accuracy    WIN / (WIN + LOSS), flats excluded. Also given: positive_move_rate = share of signed moves > 0.
  MFE / MAE               largest favourable / adverse signed excursion from the reference between signal and horizon (both reported >= 0).
                          LIVE_SAMPLES (about 1 sample per second) when tracking was continuous (no gap > {SR_LIVE_MAX_GAP:.0f} s, no restart), else CANDLE_1M using only
                          1-min candles lying fully inside the window. Both versions are stored for comparison.
  Target / stop touches   the signals have no target or stop of their own. The existing futures-trade rule (FUT_TARGET_PTS / FUT_STOP_PTS) is applied in the signal
                          direction as a hypothetical bracket, on both instruments. Nearest support/resistance touches are recorded for spot.
  Reversal                the same signal flipping to the opposite direction before the horizon. Invalidation = any change of its state.
  Conflicts               other signals in the opposite state at the trigger (opposing_signals) and the maximum number opposing during the window.
  Trade links             when a paper trade opens, every signal whose open event matches is linked with a role (see trade_role). These are associations:
                          only the combined verdict can open signal trades, and spreads are priced on options, not on the index move.
  Trade profitability     net P&L of linked trades (after the script's modelled charges, including partial exits). Kept completely separate from directional accuracy.
  Statistics              per signal, horizon and instrument: counts, wins/losses/flats, accuracy, average/median signed move, average/median MFE/MAE, reversal rate,
                          target/stop touch rates. Only onset_quality CLEAN or SESSION_OPEN events are used. A Wilson 95% interval is shown only with >= {SR_MIN_N}
                          decisive outcomes; below that the figures are marked INSUFFICIENT SAMPLE. Overlapping signals are not independent.

FLAGS (missing_data_flags)
  NO_FRESH_SPOT_REFERENCE / NO_FRESH_FUT_REFERENCE   no usable quote at the signal; that instrument's outcomes are UNRESOLVED_NO_REFERENCE
  NO_FUTURES_CONTRACT / NO_DELTA_DATA / NO_SPOT_CANDLES_TODAY   inputs missing at the trigger
  INDICATOR_VALUES_UNAVAILABLE   the signal's internal values could not be captured for this event (detail text is still recorded)
  ONSET_TIME_UNCERTAIN           the true onset time is unknown (startup / restart)
  OBS_FROM_1M_CANDLE             observed price came from a candle close, not a live tick
  LIVE_TRACKING_GAP_OR_RESTART / NO_LIVE_SAMPLES   live excursions not usable; candle method used where complete
  CANDLE_BARS_MISSING            some 1-min candles in the window are missing
  EXCURSION_FROM_CANDLES_EXCLUDES_PARTIAL_EDGE_MINUTES   candle MFE/MAE ignore the partial first and last minute
  EXCURSION_UNAVAILABLE          neither method had complete data
  EVOLUTION_MAY_MISS_CHANGES_DURING_DOWNTIME   the script was off during part of the window
  HORIZON_AFTER_15:30 / NO_PRICE_AT_HORIZON / NO_REFERENCE_PRICE_AT_SIGNAL   explain non-FINAL outcome rows

COLLECTION LIMITATIONS
  - Order-flow delta (signals 8 and 12) is ESTIMATED from tick direction vs bid/ask; the feed has no aggressor flag. It starts empty at every start.
  - The NIFTY spot index has no volume; volume-based signals use futures volume derived from cumulative-volume differences between ticks.
  - Live excursions sample about once per second: very brief spikes between samples can be missed (the candle method catches them only for full minutes).
  - Option-chain OI (signal 6) is refreshed every 60 s, so its data can be up to a minute old at the trigger.
  - Candles after a restart come partly from Upstox history, which can differ slightly from candles built live from ticks.
  - While the script is not running nothing is observed: signals that fire and end during downtime are never recorded.
  - Trade links are associations, not causes. Paper fills use bid/ask or LTP +/- SLIP_FALLBACK; charges are the script's model.
  - The dashboard's own scorecard (signal_score.json) is unchanged and uses its own rules (15-min de-dup, horizons 15/30/60 min); numbers will differ.
  - Settings can be changed while running; the values in force are stored with every event (thresholds_json).
  - If _tracker_state.json is lost, pending events are rebuilt from signal_events.csv and finalized from candles; their live tracking is marked interrupted.
  - Raw ticks (when RECORD_TICKS is on) are saved separately in spread_data/ticks/ and can be joined on time for deeper study.

DATA DICTIONARY: signal_events.csv
{W(SR_EV_COLS)}

DATA DICTIONARY: signal_outcomes.csv
{W(SR_OUT_COLS)}

DATA DICTIONARY: signal_state_changes.csv
{W(SR_CHG_COLS)}

daily_signal_report.json
  read_first        caveats to read before using the numbers
  all_time.by_signal.<key>
     events_total / events_by_onset_quality / events_used_for_stats / bull / bear
     events_linked_to_trade / events_without_trade   (linked = DIRECT_CAUSE or CONTRIBUTING_AGREE)
     horizons.<SPOT|FUT>.<1|5|15|30>    statistics as defined above (n_final, wins, losses, flats, directional_accuracy_pct, accuracy_95ci_wilson_pct,
                                         avg/median signed move pts and %, avg/median MFE/MAE, reversal_rate_pct, target/stop touch rates, sample_note)
     conditions_15m_spot                 the 15-min spot result split by conditions at the trigger (ticked, strength, time of day, confirmations, conflicts,
                                         verdict agreement, trigger type, linked to a trade)
     trade_profitability.by_role         n, wins, losses, total/avg/median net P&L of linked trades
  all_time.pairwise_at_trigger           for events of A, how often B was already agreeing / opposing (contradiction check)
  all_time.co_firing                     other signals firing within 5 min of A, same or opposite direction (redundancy check)
  all_time.data_quality                  counts of flags, outcome statuses, observation and excursion methods
  days.<YYYY-MM-DD>.by_signal            the same per-signal horizon statistics for each trading day
"""

def _sr_write_readme():
    try:
        txt = _sr_readme_text(); old = open(SR_README, encoding="utf-8").read() if os.path.exists(SR_README) else None
        if old != txt:
            with open(SR_README, "w", encoding="utf-8") as fh: fh.write(txt)
    except Exception as e: _sr_err("readme", e)

# >>> CLOUD HELPERS (added for the Render version; harmless on your own PC)
ON_CLOUD = bool(os.environ.get("RENDER"))
def _cloud_token():
    t = os.environ.get("UPSTOX_TOKEN", "").strip()
    if t: return t
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")
        if os.path.exists(p): return open(p, encoding="utf-8").read().strip().splitlines()[0].strip()
    except Exception: pass
    return ""
def _cloud_setup():
    if hasattr(time, "tzset"):
        os.environ.setdefault("TZ", "Asia/Kolkata"); time.tzset()
    if not ON_CLOUD: return
    import base64, hmac
    pw = os.environ.get("DASH_PASSWORD", "")
    if not pw:
        print("DASH_PASSWORD is not set: refusing to put the dashboard on the internet without a password."); sys.exit(1)
    want = ("Basic " + base64.b64encode(f"{os.environ.get('DASH_USER', 'trader')}:{pw}".encode()).decode()).encode()
    @app.before_request
    def _cloud_auth():
        if request.path == "/health": return None
        if hmac.compare_digest(request.headers.get("Authorization", "").encode("utf-8", "replace"), want): return None
        return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Nifty dashboard"'})
    app.add_url_rule("/health", "cloud_health", lambda: ("ok | " + persist.status_line(), 200, {"Content-Type": "text/plain"}))
# <<< CLOUD HELPERS

# ------------------------------------------------------------------ main
def main():
    global TOKEN, HDR, EXPIRY0, session, state
    print(__doc__)
    _cloud_setup()
    TOKEN = _cloud_token()
    if not TOKEN and ON_CLOUD: print("UPSTOX_TOKEN is not set."); sys.exit(1)
    if not TOKEN:
        try: TOKEN = getpass.getpass("Paste your Upstox Analytics Token (input hidden): ").strip()
        except Exception: TOKEN = input("Paste your Upstox Analytics Token: ").strip()
    HDR = {"Accept": "application/json", "Authorization": f"Bearer {TOKEN}"}
    print("Token OK.")
    load_settings(); globals()["BACKTEST_MODE"] = False; load_opts(); ensure_ledger(); load_account()
    autolab.init(globals()); autolab.register_routes(app); globals()["HTML"] = autolab.inject(HTML)
    try:
        TRIGGERS[:] = json.load(open(TRIG_PATH)) if os.path.exists(TRIG_PATH) else []
        for _t in TRIGGERS: _t["last"] = None
    except Exception: TRIGGERS[:] = []
    today = str(now_ist().date())
    session = json.load(open(SESSION_PATH)) if os.path.exists(SESSION_PATH) else {}
    if session.get("date") != today: session = dict(date=today, realized=0.0, n=0)
    EXPIRY0 = choose_expiry(); print("Expiry:", EXPIRY0)
    plan0 = pick_legs(fetch_chain(EXPIRY0)); globals()["PLAN0"] = plan0
    saved = json.load(open(STATE_PATH)) if os.path.exists(STATE_PATH) else None
    loaded = []
    if saved: loaded = saved.get("positions") if "positions" in saved else ([dict(saved, pid=1)] if saved.get("status") else [])
    PID[0] = max([saved.get("pidc", 0) if saved else 0]+[p_.get("pid", 0) for p_ in loaded])
    for p_ in loaded:
        if not p_.get("pid"): PID[0] += 1; p_["pid"] = PID[0]
    POSITIONS.clear()
    if START != "fresh":
        POSITIONS.extend(p_ for p_ in loaded if p_["status"] == "OPEN" and date.fromisoformat(p_["expiry"]) >= now_ist().date())
        n_open = len(POSITIONS)
        POSITIONS.extend(p_ for p_ in loaded if p_["status"] == "CLOSED" and p_["exit"]["time"][:10] == today)
        if n_open: say(f"RESUMED {n_open} open trade(s). Nothing was re-entered."); 
    if any(p_["status"] == "OPEN" for p_ in loaded) and not any(p_["status"] == "OPEN" for p_ in POSITIONS):
        shutil.copy(STATE_PATH, STATE_PATH + ".bak"); print("Saved open trade(s) NOT resumed. Backup: state.json.bak")
    if not POSITIONS:
        PID[0] += 1; ns = new_state(plan0); ns["pid"] = PID[0]; ns["origin"] = "startup"; POSITIONS.append(ns)
    elif not any(p_["status"] == "OPEN" for p_ in POSITIONS): say("Today's trade is already closed. Staying flat. Use the Enter buttons to trade again.")
    SEL["pid"] = POSITIONS[-1]["pid"]
    set_cur_legs()
    start_feed(feed_keys())
    t = threading.Thread(target=engine, daemon=True); t.start(); threading.Thread(target=tick_writer, daemon=True).start()
    threading.Thread(target=feed_watchdog, daemon=True).start()
    if USE_REST_FALLBACK: threading.Thread(target=rest_poller, daemon=True).start()
    if ON_CLOUD: persist.start_background(on_stop=ENGINE_STOP.set)   # saves data every 10 min, on shutdown, and restarts the app daily at 08:30 IST
    threading.Thread(target=lambda: app.run(host=("0.0.0.0" if ON_CLOUD else "127.0.0.1"), port=PORT, threaded=True, use_reloader=False), daemon=True).start()
    url = f"http://127.0.0.1:{PORT}"; print(f"\nDashboard: {url}   (Ctrl+C here to stop; an open trade stays saved)\n")
    if not ON_CLOUD: time.sleep(1.5); webbrowser.open(url)
    try:
        while t.is_alive(): time.sleep(0.5)
        time.sleep(2)
    except KeyboardInterrupt:
        ENGINE_STOP.set(); t.join(timeout=10)

HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>NIFTY Spread Paper Dashboard</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
*{box-sizing:border-box}body{margin:0;background:#0a0e14;color:#d6dde6;font:13px system-ui,Segoe UI,sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}
button,select,input{font:inherit;color:#d6dde6;background:#1a222c;border:1px solid #2c3743;border-radius:5px;padding:5px 10px}
button{cursor:pointer}button:hover{background:#243040}
.top{display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid #1d2630;flex-wrap:wrap}
.pill{padding:2px 10px;border-radius:10px;color:#fff;font-size:12px}
.ctrl{display:flex;gap:8px;padding:8px 12px;border-bottom:1px solid #1d2630;align-items:center;flex-wrap:wrap}
.red{background:#8c2a2a;border-color:#b33}.red:hover{background:#b33}.green{background:#1f6b45;border-color:#2a8}.green:hover{background:#2a8}.on{background:#1f4f8c;border-color:#378ADD}
.main{flex:1;display:grid;grid-template-columns:1fr 390px;min-height:0}
.left{display:flex;flex-direction:column;min-width:0;min-height:0}
.chartbar{display:flex;gap:6px;padding:6px 10px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #1d2630}
.seg button{border-radius:0;margin:0 -1px 0 0}.seg button.act{background:#1f4f8c;border-color:#378ADD}
.cwrap{flex:1;display:flex;min-height:0}
.tools{display:flex;flex-direction:column;gap:4px;padding:6px;border-right:1px solid #1d2630;width:46px;align-items:center}
.tools button{width:34px;height:34px;padding:0;font-size:15px}.tools button.act{background:#1f4f8c;border-color:#378ADD}
.tools input[type=color]{width:34px;height:26px;padding:0}
#chart{flex:1;position:relative;min-width:0}#overlay{position:absolute;left:0;top:0;pointer-events:none;z-index:5}
#legend{position:absolute;left:10px;top:6px;z-index:6;font-size:12px;pointer-events:none;color:#9fb0c2}
.right{border-left:1px solid #1d2630;overflow-y:auto;padding:8px}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px}
.card{border:1px solid #232d39;border-radius:7px;padding:6px 9px;background:#10161d}.card .l{font-size:11px;opacity:.65}.card .v{font-size:17px;font-weight:600}.card .s{font-size:11px;opacity:.6}
table{width:100%;border-collapse:collapse;margin:6px 0 10px}td,th{padding:3px 5px;text-align:right;border-bottom:1px solid #1d2630;font-size:12px}td:first-child,th:first-child{text-align:left}
h4{margin:10px 0 3px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;opacity:.7}
.up{color:#26a69a}.dn{color:#ef5350}
#toasts{position:fixed;right:14px;bottom:14px;display:flex;flex-direction:column;gap:6px;z-index:99}
.toast{background:#18222d;border-left:3px solid #378ADD;padding:8px 12px;border-radius:5px;max-width:380px;box-shadow:0 4px 14px #0008}
.set{display:grid;grid-template-columns:1fr 110px;gap:4px;align-items:center}.set input,.set select{width:100%;padding:3px 6px}
.log{white-space:pre-wrap;font-size:11px;opacity:.75;font-family:ui-monospace,Consolas,monospace}

.nav button{border-radius:0;margin:0 -1px 0 0}.nav button.act{background:#1f4f8c;border-color:#378ADD}
.page{display:none;grid-column:1/-1;overflow:auto;padding:14px}
.main{grid-template-columns:1fr 390px}
body[data-view=chart] .main,body[data-view=position] .main,body[data-view=trades] .main,body[data-view=settings] .main{grid-template-columns:1fr}
body[data-view=chart] #vPanel,body[data-view=trades] #vPanel,body[data-view=settings] #vPanel{display:none}
body[data-view=position] #vChart,body[data-view=trades] #vChart,body[data-view=settings] #vChart{display:none}
body[data-view=position] #vPanel{border-left:none;padding:14px}
body[data-view=position] #cards,body[data-view=position] #nets,body[data-view=position] #acctc{grid-template-columns:repeat(4,1fr)}
body[data-view=position] #pnlchart{height:230px}
body[data-view=trades] #vTrades,body[data-view=settings] #vSettings{display:block}
#vSettings .set{max-width:560px;grid-template-columns:1fr 170px;gap:8px}
#tstats{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-bottom:10px}
#ttable{overflow-x:auto}#ttable td,#ttable th{white-space:nowrap}
a.btn{display:inline-block;padding:5px 10px;border:1px solid #2c3743;border-radius:5px;background:#1a222c;color:#d6dde6;text-decoration:none;margin-right:6px}
#pnlchart{height:150px;border:1px solid #1d2630;border-radius:6px;margin-bottom:8px}

#trigform{display:none;position:fixed;z-index:60;background:#18222d;border:1px solid #ff7a1a;border-radius:8px;padding:10px 12px;width:270px;box-shadow:0 8px 24px #000b}
#trigform .set{grid-template-columns:90px 1fr;margin:8px 0}
.badge{padding:1px 8px;border-radius:9px;font-size:11px;color:#000}
.xbtn{padding:0 7px}
</style><style>
:root{--bg:#0a0e14;--panel:#101722;--panel2:#141d2b;--line:#1e2a3a;--text:#e6edf5;--mut:#8696ab;--acc:#3b82f6;--up:#22c55e;--dn:#ef4444}
body{background:var(--bg);color:var(--text);font:13px/1.45 Inter,-apple-system,"Segoe UI",system-ui,sans-serif}
::-webkit-scrollbar{width:8px;height:8px}::-webkit-scrollbar-thumb{background:#243349;border-radius:8px}
button,select,input{background:var(--panel2);border:1px solid var(--line);border-radius:8px;color:var(--text);transition:.15s}
button:hover,select:hover{border-color:#34507a;background:#1a2638}button:active{transform:scale(.97)}
input:focus,select:focus{outline:none;border-color:var(--acc);box-shadow:0 0 0 3px #3b82f633}
.top{background:var(--panel);padding:10px 18px;gap:14px;border-bottom:1px solid var(--line)}
.top>b{font-size:16px;letter-spacing:.2px}
.pill{font-weight:600;letter-spacing:.3px;padding:3px 11px}
.nav{display:flex;gap:4px;background:var(--bg);padding:3px;border-radius:11px;border:1px solid var(--line)}
.nav button{border:0;border-radius:8px;margin:0;background:transparent;color:var(--mut);padding:6px 13px;font-weight:500}
.nav button.act{background:var(--acc);color:#fff;box-shadow:0 2px 10px #3b82f655}.nav button:hover:not(.act){color:var(--text);background:#17222f}
#spotbig{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}#pnlbig{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}
#pnlbig small{font-size:11px;font-weight:500;color:var(--mut);margin-left:6px}
.ctrl{background:var(--panel);padding:9px 18px;gap:10px;border-bottom:1px solid var(--line)}
.ctrl button{padding:7px 16px;font-weight:600}
.red{background:linear-gradient(#c0392b,#962d22);border-color:#e0584a}.red:hover{background:linear-gradient(#d8483a,#a93327)}
.green{background:linear-gradient(#1f9d5c,#17744a);border-color:#2fc07a}.green:hover{background:linear-gradient(#27b46d,#1b8657)}
.on{background:linear-gradient(#2c6ed1,#1f56ab);border-color:#4f8fe8}
#sline{color:var(--mut)}
.chartbar{background:var(--panel);border-bottom:1px solid var(--line);padding:7px 14px;gap:8px}
.seg{display:inline-flex;background:var(--bg);padding:3px;border-radius:10px;border:1px solid var(--line);gap:2px}
.seg button{border:0;border-radius:7px;margin:0;background:transparent;color:var(--mut);padding:4px 11px}
.seg button.act{background:#22344f;color:#fff}.seg button:hover:not(.act){color:var(--text)}
.chartbar button.q{padding:4px 10px;color:var(--mut)}
.tools{background:var(--panel);border-right:1px solid var(--line);width:52px;gap:5px;padding:8px 0}
.tools button{width:36px;height:36px;border-radius:9px;background:transparent;border-color:transparent;color:var(--mut)}
.tools button:hover{background:#1a2638;color:var(--text)}.tools button.act{background:var(--acc);color:#fff;border-color:var(--acc)}
.right{background:var(--panel);border-left:1px solid var(--line);padding:12px}
.card{background:linear-gradient(160deg,#151f2e,#0f1722);border:1px solid var(--line);border-radius:12px;padding:10px 13px}
.card .l{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);opacity:1}
.card .v{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums;margin:2px 0}.card .s{color:var(--mut);opacity:1}
h4{color:var(--mut);opacity:1;margin:14px 0 6px;font-size:11px;letter-spacing:.09em}
table{border-radius:10px;overflow:hidden}th{color:var(--mut);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.04em;background:#0e1520}
td,th{padding:6px 9px;border-bottom:1px solid var(--line)}tr:hover td{background:#121b29}
#pnlchart{border-radius:12px;overflow:hidden;border:1px solid var(--line)}
#chart{background:var(--bg)}#legend{background:#0a0e14cc;padding:3px 8px;border-radius:7px;font-variant-numeric:tabular-nums}
.toast{background:var(--panel2);border:1px solid var(--line);border-left:4px solid var(--acc);border-radius:10px;box-shadow:0 10px 30px #000a;animation:tin .25s}
@keyframes tin{from{transform:translateX(30px);opacity:0}}
#trigform,#help{background:var(--panel2);border-radius:14px;box-shadow:0 20px 50px #000c}
#help{display:none;position:fixed;z-index:70;left:50%;top:50%;transform:translate(-50%,-50%);padding:20px 26px;border:1px solid var(--line);min-width:340px}
#help kbd{background:#22344f;border-radius:5px;padding:1px 7px;margin-right:8px;font-family:inherit}
.page{padding:20px 24px}a.btn{background:var(--panel2);border-radius:8px;border-color:var(--line)}
body.full .top,body.full .ctrl{display:none}
</style><style>
#chcol{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0}#chart{min-height:140px}
#ovbar{display:none;gap:6px;flex-wrap:wrap;padding:4px 10px;background:var(--panel);border-bottom:1px solid var(--line);font-size:12px}
.ichip{display:inline-flex;align-items:center;gap:7px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:2px 4px 2px 9px;font-variant-numeric:tabular-nums}
.ichip.off{opacity:.45}.ichip button,.phd button{padding:0 5px;border:0;background:transparent;color:var(--mut);font-size:11px}.ichip button:hover,.phd button:hover{color:var(--text)}
#subs{display:flex;flex-direction:column;max-height:48vh;overflow-y:auto;flex:none}
.sub{position:relative;height:135px;flex:none;border-top:1px solid var(--line)}.sub .cn{position:absolute;inset:0}
.phd{position:absolute;left:8px;top:3px;z-index:6;font-size:11.5px;background:#0a0e14dd;padding:1px 4px 1px 8px;border-radius:7px;display:flex;gap:7px;align-items:center;font-variant-numeric:tabular-nums}
#indDlg{display:none;position:fixed;z-index:80;left:50%;top:50%;transform:translate(-50%,-50%);width:min(780px,94vw);max-height:82vh;background:var(--panel2);border:1px solid var(--line);border-radius:14px;box-shadow:0 20px 50px #000c;flex-direction:column}
#indDlg .ihead{display:flex;gap:10px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line)}#indq{width:260px}
#indDlg .ibody{display:grid;grid-template-columns:270px 1fr;overflow:hidden;min-height:320px;flex:1}
#indlist{overflow-y:auto;padding:4px 12px 10px;border-right:1px solid var(--line)}#indlist button{display:block;width:100%;text-align:left;margin:3px 0}
#indact{overflow-y:auto;padding:8px 14px}.arow{display:flex;flex-wrap:wrap;gap:9px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line)}
.arow label{min-width:150px}.arow input[type=number]{width:64px;padding:3px 6px}.arow input[type=color]{width:26px;height:24px;padding:0}
#indDlg .ifoot{padding:8px 16px;color:var(--mut);font-size:11.5px;border-top:1px solid var(--line)}
#sigTop{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}#sigCards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px}
body[data-view=signals] .main{grid-template-columns:1fr}body[data-view=signals] #vPanel,body[data-view=signals] #vChart{display:none}body[data-view=signals] #vSignals{display:block}
body[data-view=bullset] .main,body[data-view=bearset] .main,body[data-view=genset] .main{grid-template-columns:1fr}
body[data-view=bullset] #vPanel,body[data-view=bearset] #vPanel,body[data-view=genset] #vPanel,body[data-view=bullset] #vChart,body[data-view=bearset] #vChart,body[data-view=genset] #vChart{display:none}
body[data-view=bullset] #vSettings,body[data-view=bearset] #vSettings,body[data-view=genset] #vSettings{display:block}
#settings{max-width:760px}.srow{display:grid;grid-template-columns:1fr 210px;gap:4px 18px;padding:11px 0;border-bottom:1px solid var(--line);align-items:center}
.srow .h{color:var(--mut);font-size:12px;margin-top:2px}.srow input:not([type=checkbox]),.srow select{width:100%}
#vSettings h3{margin:0 0 4px;font-size:18px}
body[data-view=directset] .main{grid-template-columns:1fr}body[data-view=directset] #vPanel,body[data-view=directset] #vChart{display:none}body[data-view=directset] #vSettings{display:block}
body[data-view=score] .main{grid-template-columns:1fr}body[data-view=score] #vPanel,body[data-view=score] #vChart{display:none}body[data-view=score] #vScore{display:block}
body.bt #chart{outline:2px solid #f5c542;outline-offset:-2px}
</style></head><body>
<div class="top"><b style="font-size:16px">NIFTY bull put spread</b><span id="pst" class="pill"></span><span id="pfeed" class="pill"></span>
<span class="seg nav" id="nav"></span><span id="spotbig"></span><span id="pnlbig"></span><span id="exp" style="color:var(--mut)"></span><span style="flex:1"></span><button id="bsnd" class="q" title="Sound alerts">🔔</button><span id="eqbig" style="margin-right:6px"></span><span id="sess"></span><span id="clock" style="opacity:.7"></span></div>
<div class="ctrl"><button id="bsq" class="red">Square off ALL</button><select id="bexp" title="Option expiry used for new trades (bull put, bear call and option buys/sells). Open trades keep their own expiry."></select><button id="benp" class="green" title="Sell a put spread now: you expect the market to hold or rise">🟢 Enter bull put</button><button id="bbuy" class="green" title="Buy now: no strategy, one position">▲ BUY</button><button id="bsell" class="red" title="Sell now: no strategy, one position">▼ SELL</button><button id="benc" class="red" style="background:linear-gradient(#b8741a,#8a5412);border-color:#e0a04a" title="Sell a call spread now: you expect the market to fall or stay down">🔴 Enter bear call</button>
<button id="bauto">Auto re-enter</button><button id="bstop">Stop (trade stays saved)</button><span id="sline" style="margin-left:10px;opacity:.85"></span></div>
<div class="main" id="main"><div class="left" id="vChart">
 <div class="chartbar"><span class="seg" id="serSeg"></span><span class="seg" id="tfSeg"></span>
  <label style="font-size:12px"><input type="checkbox" id="keep"> keep tool</label> <label style="font-size:12px" title="snap clicks to the nearest candle open/high/low/close"><input type="checkbox" id="magnet" checked> magnet</label><span id="clopts" style="display:none;font-size:12px"> Snap to <select id="snapMode"><option value="auto">Auto (nearest to my click)</option><option value="high">High (top of wick)</option><option value="low">Low (bottom of wick)</option><option value="top">Body top</option><option value="bottom">Body bottom</option><option value="open">Open</option><option value="close">Close</option></select> <select id="extMode"><option value="right">ray → to the right</option><option value="both">full width ↔</option></select></span><span id="rpbar" style="display:none;gap:6px;align-items:center"><b style="color:#f5c542">BACKTEST</b><select id="rpDate"></select><select id="rpKey" style="max-width:150px"></select>
 <button id="rpPlay" class="green">▶ Play</button><button id="rpRestart" title="Back to the very start of that day">⏮</button>
 <select id="rpSpeed"><option value="1">Real time (1×)</option><option value="5">5×</option><option value="10">10×</option><option value="30">30×</option><option value="60">60×</option><option value="300">300×</option><option value="max">As fast as possible</option></select>
 <input type="range" id="rpSlider" min="0" max="1000" value="0" style="width:150px"><span id="rpClock" style="font-variant-numeric:tabular-nums"></span><span id="rpSrc" style="color:var(--mut);font-size:12px"></span></span><select id="possel" title="Which trade the chart and its lines follow" style="max-width:230px"></select><span id="hint" style="color:var(--mut);font-size:12px"></span><span style="flex:1"></span><button class="q" id="bInd" title="Indicators (I)">ƒx Indicators</button><button class="q" id="bLive" title="Jump to latest candle">▶ Live</button><button class="q" id="bFit" title="Fit all candles">⤢ Fit</button><button class="q" id="bFull" title="Fullscreen chart">⛶</button><button class="q" id="bHelp" title="Keyboard shortcuts">?</button></div>
 <div class="cwrap"><div class="tools" id="tools"></div><div id="chcol"><div id="ovbar"></div><div id="chart"><div id="legend"></div><canvas id="overlay"></canvas></div><div id="subs"></div></div></div></div>
<div class="right" id="vPanel"><div id="acct"></div><div id="poslist"></div><div id="cards" class="cards"></div><div id="pnlchart"></div><div id="details"></div><div id="legs"></div><div id="nets" class="cards"></div><div id="rules"></div>
 <h4>Trigger lines</h4><div id="trigs"></div>
 <h4>Log</h4><div id="log" class="log"></div></div>
<div class="page" id="vTrades"><div id="tstats"></div>
 <p><a class="btn" href="/api/trades.csv" download>Download trades.csv</a><a class="btn" href="/api/events.csv" download>Download trade_events.csv</a>
 <span style="opacity:.6">also saved in the spread_data folder: one row per closed trade with full data, plus an entry/exit event log</span></p><div id="ttable"></div></div>
<div class="page" id="vSignals"><div id="sigFut" style="margin-bottom:10px;color:var(--mut)"></div><div id="sigTop"></div><div id="sigCards"></div><h4>Support and resistance: where price has turned back</h4><div id="sigSR"></div><h4>Signal history</h4><div id="sigLog"></div>
 <p style="color:var(--mut)">Your bull put and bear call choices (which strikes, how many lots, when to take profit or stop) are in the <b>⚙ Settings → Options</b> tab. Each signal says what it expects over the next few 5-minute candles. Everything here is for same-day trading.</p></div>
<div class="page" id="vScore"><h3 style="margin:0 0 4px">Signal scorecard</h3><p style="color:var(--mut);max-width:860px">Each time a signal turns bullish or bearish, the platform notes Nifty's price and checks again 15, 30 and 60 minutes later. <b>Right</b> = Nifty is at least <span id="scMinMove">8</span> points higher (for a bullish signal) or lower (for a bearish one). <b>Wrong</b> = it is at least that far the other way. Smaller moves are ignored, so the percentage only counts clear moves ("of N" = how many were clear). <b>Average move</b> is in Nifty points in the signal's direction (plus is good). The same signal is counted at most once every 15 minutes. A few days of data are needed before the percentages mean much.</p>
 <div id="scoreTable"></div><h4>Latest signals and what happened next</h4><div id="scoreEvents"></div><button id="scoreClear" style="margin:12px 0">Reset scorecard</button></div>
<div class="page" id="vSettings"><div class="seg" id="subnav" style="margin-bottom:12px"></div><h3 id="setTitle"></h3><p id="setNote" style="color:var(--mut);max-width:760px"></p><div id="settings"></div><button id="applyset" class="green" style="margin:14px 0">Save changes</button>
 <p style="color:var(--mut);max-width:760px">Profit target, stop loss and the end-of-day time apply straight away, even to a trade that is open. Which strikes to pick and how many lots apply to your next trade.</p></div></div>
<div id="trigform"><b>⚡ Trigger line</b><div id="tf_series" style="font-size:11px;opacity:.65"></div>
<div class="set"><label>Price</label><input id="tf_price">
<label>Action</label><select id="tf_act"><option value="EXIT">Exit trade</option><option value="ENTER">Enter trade</option><option value="ALERT">Alert only</option></select>
<label>When price</label><select id="tf_dir"><option value="auto">moves to the line</option><option value="up">crosses up ↑</option><option value="down">crosses down ↓</option><option value="any">crosses either way</option></select>
<label>One-time</label><input type="checkbox" id="tf_once" checked></div>
<button id="tf_add" class="green">Add line</button> <button id="tf_cancel">Cancel</button></div>
<div id="help"><b style="font-size:15px">Keyboard shortcuts</b><p><kbd>V</kbd>cursor &nbsp;<kbd>T</kbd>trend line &nbsp;<kbd>R</kbd>ray</p><p><kbd>H</kbd>horizontal &nbsp;<kbd>C</kbd>candle line &nbsp;<kbd>A</kbd>⚡ trigger line</p>
<p><kbd>F</kbd>fib &nbsp;<kbd>M</kbd>measure &nbsp;<kbd>B</kbd>rectangle &nbsp;<kbd>X</kbd>text</p><p><kbd>1</kbd>–<kbd>6</kbd>timeframe 1m…1h &nbsp;<kbd>Del</kbd>delete selected &nbsp;<kbd>Esc</kbd>cancel</p>
<p><kbd>Ctrl+Z</kbd>undo drawing &nbsp;<kbd>?</kbd>this help &nbsp;<kbd>I</kbd>indicators</p><button onclick="$('help').style.display='none'">Close</button></div><div id="indDlg"><div class="ihead"><b style="font-size:15px">Indicators</b><input id="indq" placeholder="Search RSI, MACD, Bollinger..." autocomplete="off"><span style="flex:1"></span><button id="indx">✕</button></div>
<div class="ibody"><div id="indlist"></div><div id="indact"></div></div>
<div class="ifoot">Volume, VWAP, MFI and OBV need traded volume. On the Nifty chart they use NIFTY futures volume (the index itself has none). The spread has none, so switch to Short leg / Long leg there. Indicators are computed from the candles on screen and remembered in this browser.</div></div><div id="toasts"></div>
<script>
const $=id=>document.getElementById(id);
const fx=(x,d=2)=>x==null?'–':Number(x).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});
const money=x=>(x>=0?'+':'−')+'₹'+Math.abs(x).toLocaleString('en-IN',{maximumFractionDigits:0});
const cls=x=>x>=0?'up':'dn';
const post=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
let S=null,kind='spot',tf=1,label='',bars=[],lastLv='',lastMk='',lines=[],lastToast=0;

/* ---------- chart ---------- */
const chart=LightweightCharts.createChart($('chart'),{autoSize:true,layout:{background:{color:'#0a0e14'},textColor:'#aab6c3'},
 grid:{vertLines:{color:'#151c24'},horzLines:{color:'#151c24'}},crosshair:{mode:0},rightPriceScale:{borderColor:'#26303b'},
 timeScale:{borderColor:'#26303b',timeVisible:true,secondsVisible:false,rightOffset:8}});
const cs=chart.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
chart.subscribeCrosshairMove(p=>{const b=p.seriesData&&p.seriesData.get(cs);$('legend').innerHTML=label+(b?`&nbsp; O ${fx(b.open)} H ${fx(b.high)} L ${fx(b.low)} C ${fx(b.close)}`:'');showVals(p.time);});
function setLevels(lv,mk){
  const s=JSON.stringify(lv); if(s!==lastLv){lastLv=s;lines.forEach(l=>cs.removePriceLine(l));lines=lv.map(l=>cs.createPriceLine({price:l.price,color:l.color,lineWidth:1,lineStyle:2,axisLabelVisible:true,title:l.title}));}
  const m=JSON.stringify(mk); if(m!==lastMk){lastMk=m;try{cs.setMarkers(mk.sort((a,b)=>a.time-b.time));}catch(e){}}
}
async function loadFull(){
 if(BT)return rpReload();
  const d=await (await fetch(`/api/candles?series=${kind}&tf=${tf}`)).json();
  label=d.label;bars=d.bars;cs.setData(bars);lastLv='';lastMk='';setLevels(d.levels,d.markers);
  const n=bars.length;if(n)chart.timeScale().setVisibleLogicalRange({from:Math.max(0,n-120),to:n+8});applyInds();
  loadDrawings();
}
async function tailPoll(){
 if(BT)return;
  try{
    const d=await (await fetch(`/api/candles?series=${kind}&tf=${tf}&tail=1`)).json();
    if(d.label!==label||(!bars.length&&d.bars.length)) return loadFull();
    let app=false;for(const b of d.bars){const l=bars[bars.length-1];
      if(!l||b.time>l.time){bars.push(b);cs.update(b);app=true;}else if(b.time===l.time){bars[bars.length-1]=b;cs.update(b);}}refreshInds(app);
    setLevels(d.levels,d.markers);
  }catch(e){}
}
function mkSeg(el,items,get,set){el.innerHTML='';items.forEach(([v,t])=>{const b=document.createElement('button');b.textContent=t;
 b.onclick=()=>{set(v);[...el.children].forEach(c=>c.classList.remove('act'));b.classList.add('act');loadFull();};
 if(get()===v)b.classList.add('act');el.appendChild(b);});}
mkSeg($('serSeg'),[['spot','Nifty'],['spread','Spread'],['short','Short leg'],['long','Long leg']],()=>kind,v=>kind=v);
mkSeg($('tfSeg'),[[1,'1m'],[3,'3m'],[5,'5m'],[15,'15m'],[30,'30m'],[60,'1h']],()=>tf,v=>tf=v);

/* ---------- drawing tools (overlay canvas) ---------- */
const cv=$('overlay'),ctx=cv.getContext('2d');
let tool='cursor',color='#f5c542',drawings=[],sel=-1,pending=null,mouse=null,down=null,trigRects=[],drag=null;
const TOOLS=[['cursor','⌖','Cursor / select'],['trend','╱','Trend line'],['ray','➚','Ray'],['h','─','Horizontal line'],['cline','🕯','Candle line: horizontal ray from a candle high / low / body / open / close'],['v','│','Vertical line'],
 ['rect','▭','Rectangle'],['fib','Fib','Fib retracement'],['measure','↔','Measure'],['text','T','Text'],['trig','⚡','Trigger line: exit / enter / alert when price crosses']];
function buildTools(){const t=$('tools');t.innerHTML='';
 TOOLS.forEach(([id,ic,tt])=>{const b=document.createElement('button');b.textContent=ic;b.title=tt;b.className=id===tool?'act':'';b.onclick=()=>{tool=id;pending=null;buildTools();hint();};t.appendChild(b);});
 const c=document.createElement('input');c.type='color';c.value=color;c.title='Colour';c.oninput=e=>{color=e.target.value;if(sel>=0){drawings[sel].color=color;saveDrawings();}};t.appendChild(c);
 [['⌫','Delete selected (Del)',()=>delSel()],['↶','Undo last',()=>{drawings.pop();sel=-1;saveDrawings();}],['🗑','Clear all',()=>{if(confirm('Clear all drawings on this chart?')){drawings=[];sel=-1;saveDrawings();}}]]
  .forEach(([ic,tt,fn])=>{const b=document.createElement('button');b.textContent=ic;b.title=tt;b.onclick=fn;t.appendChild(b);});}
function hint(){$('clopts').style.display=tool==='cline'?'inline':'none';$('hint').textContent=tool==='cursor'?'click a drawing to select · Del removes it':(tool==='cline'?'hover a candle, click to draw a line from its high / low / body across the chart · pick the snap point above':tool==='trig'?'click the price level, then pick the action · drag a ⚡ line to move it, ✕ to delete':['h','v','text'].includes(tool)?'click on chart':'click start point, then end point')+' · Esc cancels';}
const dkey=()=>'drw:'+label;
function saveDrawings(){try{localStorage.setItem(dkey(),JSON.stringify(drawings));}catch(e){}}
function loadDrawings(){try{drawings=JSON.parse(localStorage.getItem(dkey())||'[]');}catch(e){drawings=[];}sel=-1;pending=null;}
function delSel(){if(sel>=0){drawings.splice(sel,1);sel=-1;saveDrawings();}}
const iv=()=>tf*60;
function l2t(L){const n=bars.length;if(!n)return 0;
 if(L<=0)return bars[0].time+L*iv();if(L>=n-1)return bars[n-1].time+(L-(n-1))*iv();
 const i=Math.floor(L);return bars[i].time+(L-i)*(bars[i+1].time-bars[i].time);}
function t2l(t){const n=bars.length;if(!n)return 0;
 if(t<=bars[0].time)return (t-bars[0].time)/iv();if(t>=bars[n-1].time)return n-1+(t-bars[n-1].time)/iv();
 let lo=0,hi=n-1;while(hi-lo>1){const m=(lo+hi)>>1;if(bars[m].time<=t)lo=m;else hi=m;}
 return lo+(t-bars[lo].time)/(bars[lo+1].time-bars[lo].time);}
const X=t=>chart.timeScale().logicalToCoordinate(t2l(t)),Y=p=>cs.priceToCoordinate(p);
function plotW(){let w=$('chart').clientWidth;try{w-=chart.priceScale('right').width();}catch(e){w-=60;}return w;}
function plotH(){let h=$('chart').clientHeight;try{h-=chart.timeScale().height();}catch(e){h-=28;}return h;}
function pt(e){const r=cv.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
 return{x,y,cx:e.clientX,cy:e.clientY,t:l2t(chart.timeScale().coordinateToLogical(x)),p:cs.coordinateToPrice(y)};}
const inPlot=q=>q.x>=0&&q.y>=0&&q.x<plotW()&&q.y<plotH()&&q.p!=null;
function place(q){
 q=snapQ(q);
 if(tool==='cline'){const b=barAt(q.x);if(!b)return;drawings.push({type:'cline',t:b.time,p:barLevel(b,$('snapMode').value,q.y),ext:$('extMode').value,color});saveDrawings();if(!$('keep').checked){tool='cursor';buildTools();hint();}return;}
 if(tool==='trig'){showTrigForm(q);return;}
 const add=o=>{drawings.push(Object.assign({color},o));saveDrawings();if(!$('keep').checked){tool='cursor';buildTools();hint();}};
 if(tool==='h')return add({type:'h',p:q.p});
 if(tool==='v')return add({type:'v',t:q.t});
 if(tool==='text'){const s=prompt('Text');if(s)add({type:'text',t:q.t,p:q.p,txt:s});return;}
 if(!pending){pending={type:tool,a:{t:q.t,p:q.p}};return;}
 add({type:pending.type,a:pending.a,b:{t:q.t,p:q.p}});pending=null;
}
const ce=$('chart');
ce.addEventListener('mousedown',e=>{const q=pt(e);if(!inPlot(q))return;
 if(tool==='cursor'){const h=trigHit(q);if(h){e.stopPropagation();e.preventDefault();if(h.close)delTrig(h.id);else drag={id:h.id,price:q.p};return;}const hc=nearestHC(q);if(hc>=0){e.stopPropagation();e.preventDefault();sel=hc;color=drawings[hc].color;ddrag={i:hc};buildTools();return;}down={x:q.x,y:q.y};return;}
 e.stopPropagation();e.preventDefault();place(q);},true);
ce.addEventListener('mouseup',e=>{if(ddrag){finishDDrag();return;}if(drag){finishDrag();return;}if(tool!=='cursor'||!down)return;const q=pt(e);
 if(Math.hypot(q.x-down.x,q.y-down.y)<4)select(q);down=null;},true);
ce.addEventListener('mousemove',e=>{mouse=pt(e);if(drag&&mouse.p!=null)drag.price=mouse.p;if(ddrag&&mouse.p!=null){let pr=mouse.p;if($('magnet').checked&&bars.length){const b=barAt(mouse.x);if(b){let bd=10;[b.open,b.high,b.low,b.close].forEach(v=>{const y=Y(v);if(y!=null&&Math.abs(y-mouse.y)<bd){bd=Math.abs(y-mouse.y);pr=v;}});}}drawings[ddrag.i].p=pr;}
 if(tool==='cursor'&&!drag&&!ddrag)ce.style.cursor=(inPlot(mouse)&&(trigHit(mouse)||nearestHC(mouse)>=0))?'ns-resize':'';},true);
window.addEventListener('mouseup',()=>{if(drag)finishDrag();finishDDrag();});
document.addEventListener('keydown',e=>{
 if(['INPUT','SELECT'].includes(document.activeElement.tagName))return;
 if(e.key==='Escape'){pending=null;tool='cursor';sel=-1;buildTools();hint();}
 if(e.key==='Delete'||e.key==='Backspace')delSel();
 if(e.ctrlKey&&e.key==='z'){drawings.pop();sel=-1;saveDrawings();}});
function segd(px,py,ax,ay,bx,by){const dx=bx-ax,dy=by-ay,l=dx*dx+dy*dy;let t=l?((px-ax)*dx+(py-ay)*dy)/l:0;t=Math.max(0,Math.min(1,t));return Math.hypot(px-(ax+t*dx),py-(ay+t*dy));}
const FIB=[0,.236,.382,.5,.618,.786,1];
function geo(d){
 if(d.type==='h')return{y:Y(d.p)};if(d.type==='v')return{x:X(d.t)};
 if(d.type==='text'||d.type==='cline')return{x:X(d.t),y:Y(d.p)};
 const ax=X(d.a.t),ay=Y(d.a.p),bx=X(d.b.t),by=Y(d.b.p);return{ax,ay,bx,by};}
function dist(d,q){const g=geo(d);
 if(d.type==='h')return Math.abs(q.y-g.y);if(d.type==='v')return Math.abs(q.x-g.x);
 if(d.type==='text')return Math.hypot(q.x-g.x,q.y-g.y);
 if(d.type==='cline'){if(g.y==null)return 1e9;if(d.ext==='right'&&q.x<g.x-6)return 1e9;return Math.abs(q.y-g.y);}
 if(d.type==='trend'||d.type==='measure')return segd(q.x,q.y,g.ax,g.ay,g.bx,g.by);
 if(d.type==='ray'){const k=5000,dx=g.bx-g.ax,dy=g.by-g.ay,l=Math.hypot(dx,dy)||1;return segd(q.x,q.y,g.ax,g.ay,g.ax+dx/l*k,g.ay+dy/l*k);}
 const x1=Math.min(g.ax,g.bx),x2=Math.max(g.ax,g.bx),y1=Math.min(g.ay,g.by),y2=Math.max(g.ay,g.by);
 if(d.type==='rect')return Math.min(segd(q.x,q.y,x1,y1,x2,y1),segd(q.x,q.y,x2,y1,x2,y2),segd(q.x,q.y,x2,y2,x1,y2),segd(q.x,q.y,x1,y2,x1,y1));
 if(d.type==='fib'){let m=1e9;FIB.forEach(f=>{const y=g.ay+(g.by-g.ay)*f;m=Math.min(m,segd(q.x,q.y,x1,y,x2,y));});return m;}
 return 1e9;}
function select(q){let best=-1,bd=7;drawings.forEach((d,i)=>{const v=dist(d,q);if(v<bd){bd=v;best=i;}});sel=best;if(best>=0){color=drawings[best].color;buildTools();}}


function barAt(x){const L=chart.timeScale().coordinateToLogical(x);if(L==null||!bars.length)return null;const i=Math.round(L);return(i>=0&&i<bars.length)?bars[i]:null;}
function barLevel(b,mode,y){const bt=Math.max(b.open,b.close),bb=Math.min(b.open,b.close);
 if(mode==='high')return b.high;if(mode==='low')return b.low;if(mode==='top')return bt;if(mode==='bottom')return bb;if(mode==='open')return b.open;if(mode==='close')return b.close;
 let best=b.high,bd=1e9;[b.high,b.low,bt,bb].forEach(v=>{const yy=Y(v);if(yy!=null&&Math.abs(yy-y)<bd){bd=Math.abs(yy-y);best=v;}});return best;}
function snapQ(q){if(!$('magnet').checked||tool==='cursor'||tool==='cline')return q;const b=barAt(q.x);if(!b)return q;let best=null,bd=14;
 [b.open,b.high,b.low,b.close].forEach(v=>{const y=Y(v);if(y!=null&&Math.abs(y-q.y)<bd){bd=Math.abs(y-q.y);best=v;}});
 return best==null?q:Object.assign({},q,{p:best,t:b.time});}
function nearestHC(q){let best=-1,bd=7;drawings.forEach((d,i)=>{if(d.type!=='h'&&d.type!=='cline')return;const v=dist(d,q);if(v<bd){bd=v;best=i;}});return best;}
let ddrag=null;
function finishDDrag(){if(ddrag){ddrag=null;saveDrawings();}}
function previewCline(W){if(tool!=='cline'||!mouse||!inPlot(mouse))return;const b=barAt(mouse.x);if(!b)return;
 const lv=barLevel(b,$('snapMode').value,mouse.y),y=Y(lv),xb=X(b.time);if(y==null||xb==null)return;
 ctx.save();ctx.strokeStyle=color;ctx.fillStyle=color;ctx.globalAlpha=.85;ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo($('extMode').value==='both'?0:xb,y);ctx.lineTo(W,y);ctx.stroke();
 ctx.setLineDash([]);ctx.beginPath();ctx.arc(xb,y,5,0,7);ctx.fill();ctx.font='11px system-ui';ctx.fillText(fx(lv),xb+8,y-6);ctx.restore();}
function trigHit(q){for(const r of trigRects){if(Math.abs(q.y-r.y)<10&&q.x>=r.cx1&&q.x<=r.x2)return{id:r.id,close:true};}
 for(const t of ((S&&S.triggers)||[])){if(t.series!==kind||t.dormant)continue;const y=Y(t.price);if(y!=null&&Math.abs(q.y-y)<6)return{id:t.id};}return null;}
function finishDrag(){const d=drag;drag=null;if(!d||d.price==null)return;post('/api/triggers',{action:'update',id:d.id,price:d.price});
 if(S&&S.triggers)S.triggers.forEach(t=>{if(t.id===d.id){t.price=d.price;t.status='armed';}});}
function delTrig(id){post('/api/triggers',{action:'delete',id});if(S&&S.triggers)S.triggers=S.triggers.filter(t=>t.id!==id);}
function showTrigForm(q){const f=$('trigform');$('tf_price').value=q.p.toFixed(2);$('tf_series').textContent='on: '+label;
 f.style.display='block';f.style.left=Math.min(q.cx+12,window.innerWidth-290)+'px';f.style.top=Math.min(q.cy-20,window.innerHeight-300)+'px';}
function hideTrigForm(){$('trigform').style.display='none';tool='cursor';buildTools();hint();}
$('tf_cancel').onclick=hideTrigForm;
$('tf_add').onclick=async()=>{const pr=parseFloat($('tf_price').value);if(isNaN(pr))return;
 await post('/api/triggers',{action:'add',series:kind,price:pr,dir:$('tf_dir').value,act:$('tf_act').value,once:$('tf_once').checked});hideTrigForm();poll();};
function drawTriggers(W){trigRects=[];
 ((S&&S.triggers)||[]).forEach(t=>{if(t.series!==kind||t.dormant)return;const price=(drag&&drag.id===t.id)?drag.price:t.price;const y=Y(price);if(y==null)return;
  const c=t.status==='fired'?'#7d8794':({EXIT:'#ff7a1a',ENTER:'#2ecc71',ALERT:'#4aa3ff'}[t.act]);
  ctx.save();ctx.strokeStyle=c;ctx.lineWidth=(drag&&drag.id===t.id)?2.5:1.6;ctx.setLineDash([8,5]);ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(W,y);ctx.stroke();ctx.restore();
  const txt=`⚡ ${t.act} ${t.dir==='up'?'↑':t.dir==='down'?'↓':'↕'} ${fx(price)}${t.once?'':' ⟳'}${t.status==='fired'?' ✓':''}`;
  ctx.font='12px system-ui';const w=ctx.measureText(txt).width+30,x0=8;ctx.fillStyle=c;ctx.fillRect(x0,y-10,w,20);ctx.fillStyle='#000';ctx.fillText(txt,x0+6,y+4);ctx.fillText('✕',x0+w-16,y+4);
  trigRects.push({id:t.id,x1:x0,x2:x0+w,cx1:x0+w-22,y});});}
function renderTrigs(){const T=(S&&S.triggers)||[];const col={EXIT:'#ff7a1a',ENTER:'#2ecc71',ALERT:'#4aa3ff'};
 $('trigs').innerHTML=T.length?'<table><tr><th>Action</th><th>Line</th><th>Level</th><th>State</th><th></th></tr>'+T.map(t=>`<tr><td><span class="badge" style="background:${col[t.act]}">${t.act}</span></td>
  <td>${t.label}</td><td>${t.dir==='up'?'↑':t.dir==='down'?'↓':'↕'} ${fx(t.price)}</td><td>${t.status==='expired'?'expired (trade closed)':t.other?('armed on trade #'+t.pid+' (not selected)'):t.dormant?'dormant (strikes changed)':t.status==='fired'?'fired ✓':'armed'+(t.pid?(' · trade #'+t.pid):'')+(t.once?'':' ⟳')}</td>
  <td><button class="xbtn" onclick="delTrig(${t.id})">✕</button></td></tr>`).join('')+'</table>':'<span style="opacity:.6">none. Use the ⚡ tool on the chart to add one.</span>';}
function drawOne(d,isSel,W){
 const g=geo(d);ctx.strokeStyle=d.color;ctx.fillStyle=d.color;ctx.lineWidth=isSel?3:1.5;ctx.font='11px system-ui';
 const tag=(txt,x,y)=>{const w=ctx.measureText(txt).width+8;ctx.fillStyle=d.color;ctx.fillRect(x,y-8,w,16);ctx.fillStyle='#000';ctx.fillText(txt,x+4,y+4);};
 if(d.type==='h'){if(g.y==null)return;ctx.beginPath();ctx.moveTo(0,g.y);ctx.lineTo(W,g.y);ctx.stroke();tag(fx(d.p),W-62,g.y);return;}
 if(d.type==='v'){if(g.x==null)return;ctx.beginPath();ctx.moveTo(g.x,0);ctx.lineTo(g.x,plotH());ctx.stroke();return;}
 if(d.type==='cline'){if(g.y==null)return;const x0=d.ext==='both'||g.x==null?0:Math.max(0,g.x);ctx.beginPath();ctx.moveTo(x0,g.y);ctx.lineTo(W,g.y);ctx.stroke();if(g.x!=null){ctx.beginPath();ctx.arc(g.x,g.y,4,0,7);ctx.fill();}tag(fx(d.p),W-62,g.y);return;}
 if(d.type==='text'){if(g.x==null||g.y==null)return;ctx.font='14px system-ui';ctx.fillText(d.txt,g.x,g.y);return;}
 if([g.ax,g.ay,g.bx,g.by].some(v=>v==null))return;
 if(d.type==='trend'||d.type==='measure'){ctx.beginPath();ctx.moveTo(g.ax,g.ay);ctx.lineTo(g.bx,g.by);ctx.stroke();}
 if(d.type==='ray'){const dx=g.bx-g.ax,dy=g.by-g.ay,l=Math.hypot(dx,dy)||1;ctx.beginPath();ctx.moveTo(g.ax,g.ay);ctx.lineTo(g.ax+dx/l*5000,g.ay+dy/l*5000);ctx.stroke();}
 if(d.type==='rect'){ctx.globalAlpha=.13;ctx.fillRect(g.ax,g.ay,g.bx-g.ax,g.by-g.ay);ctx.globalAlpha=1;ctx.strokeRect(g.ax,g.ay,g.bx-g.ax,g.by-g.ay);}
 if(d.type==='fib'){const x1=Math.min(g.ax,g.bx),x2=Math.max(g.ax,g.bx);FIB.forEach(f=>{const y=g.ay+(g.by-g.ay)*f,p=d.a.p+(d.b.p-d.a.p)*f;
   ctx.globalAlpha=.8;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x1,y);ctx.lineTo(x2,y);ctx.stroke();ctx.globalAlpha=1;ctx.fillText(`${f}  (${fx(p)})`,x1+4,y-3);});}
 if(d.type==='measure'){const dp=d.b.p-d.a.p,pc=dp/d.a.p*100,nb=Math.round(t2l(d.b.t)-t2l(d.a.t));
   const txt=`${dp>=0?'+':''}${fx(dp)} (${pc.toFixed(2)}%)  ${nb} bars`;ctx.font='12px system-ui';const w=ctx.measureText(txt).width+10;
   ctx.fillStyle=dp>=0?'#26a69a':'#ef5350';ctx.fillRect(g.bx+6,g.by-10,w,20);ctx.fillStyle='#000';ctx.fillText(txt,g.bx+11,g.by+4);}
}
function frame(){
 const w=$('chart').clientWidth,h=$('chart').clientHeight,r=window.devicePixelRatio||1;
 if(cv.width!==w*r||cv.height!==h*r){cv.width=w*r;cv.height=h*r;cv.style.width=w+'px';cv.style.height=h+'px';}
 ctx.setTransform(r,0,0,r,0,0);ctx.clearRect(0,0,w,h);
 ctx.save();ctx.beginPath();ctx.rect(0,0,plotW(),plotH());ctx.clip();const W=plotW();
 try{drawings.forEach((d,i)=>drawOne(d,i===sel,W));drawTriggers(W);previewCline(W);drawDots();
  if(pending&&mouse&&mouse.p!=null){ctx.globalAlpha=.7;drawOne(Object.assign({color},{type:pending.type,a:pending.a,b:{t:snapQ(mouse).t,p:snapQ(mouse).p}}),false,W);ctx.globalAlpha=1;}}catch(e){}
 ctx.restore();requestAnimationFrame(frame);}
buildTools();hint();requestAnimationFrame(frame);

/* ================= INDICATORS: maths (computed in the browser from the candles on screen) ================= */
const nul=n=>new Array(n).fill(null),fst=a=>a.findIndex(x=>x!=null);
function sma(a,p){const n=a.length,o=nul(n),s0=fst(a);if(s0<0||p<1)return o;let s=0;for(let i=s0;i<n;i++){s+=a[i];if(i-s0>=p)s-=a[i-p];if(i-s0>=p-1)o[i]=s/p;}return o;}
function ema(a,p,k){const n=a.length,o=nul(n),s0=fst(a);if(s0<0||p<1||n-s0<p)return o;k=k||2/(p+1);let e=0;for(let i=s0;i<s0+p;i++)e+=a[i];e/=p;o[s0+p-1]=e;for(let i=s0+p;i<n;i++){e=a[i]*k+e*(1-k);o[i]=e;}return o;}
const rma=(a,p)=>ema(a,p,1/p);
function wma(a,p){const n=a.length,o=nul(n),s0=fst(a),d=p*(p+1)/2;if(s0<0)return o;for(let i=s0+p-1;i<n;i++){let s=0;for(let j=0;j<p;j++)s+=a[i-p+1+j]*(j+1);o[i]=s/d;}return o;}
function rmax(a,p){const n=a.length,o=nul(n);for(let i=p-1;i<n;i++){let m=-Infinity,ok=1;for(let j=i-p+1;j<=i;j++){if(a[j]==null){ok=0;break;}if(a[j]>m)m=a[j];}if(ok)o[i]=m;}return o;}
function rmin(a,p){const n=a.length,o=nul(n);for(let i=p-1;i<n;i++){let m=Infinity,ok=1;for(let j=i-p+1;j<=i;j++){if(a[j]==null){ok=0;break;}if(a[j]<m)m=a[j];}if(ok)o[i]=m;}return o;}
function stdev(a,p){const n=a.length,o=nul(n),m=sma(a,p);for(let i=0;i<n;i++){if(m[i]==null)continue;let s=0;for(let j=i-p+1;j<=i;j++)s+=(a[j]-m[i])*(a[j]-m[i]);o[i]=Math.sqrt(s/p);}return o;}
const trng=Q=>Q.h.map((h,i)=>i?Math.max(h-Q.l[i],Math.abs(h-Q.c[i-1]),Math.abs(Q.l[i]-Q.c[i-1])):h-Q.l[i]);
function rsi(c,p){const n=c.length,o=nul(n);if(n<=p||p<1)return o;let g=0,l=0;for(let i=1;i<=p;i++){const d=c[i]-c[i-1];if(d>=0)g+=d;else l-=d;}g/=p;l/=p;o[p]=l===0?100:100-100/(1+g/l);
 for(let i=p+1;i<n;i++){const d=c[i]-c[i-1];g=(g*(p-1)+Math.max(d,0))/p;l=(l*(p-1)+Math.max(-d,0))/p;o[i]=l===0?100:100-100/(1+g/l);}return o;}
function stochRaw(c,h,l,p){const hh=rmax(h,p),ll=rmin(l,p);return c.map((x,i)=>hh[i]==null?null:(hh[i]===ll[i]?50:(x-ll[i])/(hh[i]-ll[i])*100));}
function cci(Q,p){const tp=Q.c.map((c,i)=>(Q.h[i]+Q.l[i]+c)/3),m=sma(tp,p),n=tp.length,o=nul(n);for(let i=p-1;i<n;i++){let d=0;for(let j=i-p+1;j<=i;j++)d+=Math.abs(tp[j]-m[i]);d/=p;o[i]=d?(tp[i]-m[i])/(0.015*d):0;}return o;}
function adx(Q,p){const n=Q.c.length,pdm=nul(n),mdm=nul(n);pdm[0]=0;mdm[0]=0;
 for(let i=1;i<n;i++){const up=Q.h[i]-Q.h[i-1],dn=Q.l[i-1]-Q.l[i];pdm[i]=(up>dn&&up>0)?up:0;mdm[i]=(dn>up&&dn>0)?dn:0;}
 const t=rma(trng(Q),p),P=rma(pdm,p),M=rma(mdm,p),pdi=nul(n),mdi=nul(n),dx=nul(n);
 for(let i=0;i<n;i++){const tt=t[i];if(tt==null||P[i]==null||M[i]==null)continue;pdi[i]=tt?100*P[i]/tt:0;mdi[i]=tt?100*M[i]/tt:0;const s=pdi[i]+mdi[i];dx[i]=s?100*Math.abs(pdi[i]-mdi[i])/s:0;}
 return[rma(dx,p),pdi,mdi];}
function aroon(Q,p){const n=Q.c.length,u=nul(n),d=nul(n);for(let i=p;i<n;i++){let hi=i-p,lo=i-p;for(let j=i-p;j<=i;j++){if(Q.h[j]>=Q.h[hi])hi=j;if(Q.l[j]<=Q.l[lo])lo=j;}u[i]=100*(p-(i-hi))/p;d[i]=100*(p-(i-lo))/p;}return[u,d];}
function mfi(Q,p){const n=Q.c.length,o=nul(n),tp=Q.c.map((c,i)=>(Q.h[i]+Q.l[i]+c)/3);for(let i=p;i<n;i++){let pos=0,neg=0;for(let j=i-p+1;j<=i;j++){const f=tp[j]*Q.v[j];if(tp[j]>tp[j-1])pos+=f;else if(tp[j]<tp[j-1])neg+=f;}o[i]=neg===0?(pos===0?50:100):100-100/(1+pos/neg);}return o;}
function obv(Q){const n=Q.c.length,o=nul(n);let s=0;for(let i=0;i<n;i++){if(i)s+=Q.c[i]>Q.c[i-1]?Q.v[i]:Q.c[i]<Q.c[i-1]?-Q.v[i]:0;o[i]=s;}return o;}
function vwap(Q){const n=Q.c.length,o=nul(n);let day=-1,pv=0,vv=0;for(let i=0;i<n;i++){const dd=Math.floor(Q.t[i]/86400);if(dd!==day){day=dd;pv=0;vv=0;}pv+=(Q.h[i]+Q.l[i]+Q.c[i])/3*Q.v[i];vv+=Q.v[i];o[i]=vv>0?pv/vv:null;}return o;}
function supertrend(Q,p,m){const n=Q.c.length,a=rma(trng(Q),p),up=nul(n),dn=nul(n);let fu=null,fl=null,dir=1,pc=null;
 for(let i=0;i<n;i++){if(a[i]==null){pc=Q.c[i];continue;}const hl=(Q.h[i]+Q.l[i])/2,bu=hl+m*a[i],bl=hl-m*a[i];
  if(fu==null){fu=bu;fl=bl;dir=Q.c[i]>=hl?1:-1;}
  else{fu=(bu<fu||pc>fu)?bu:fu;fl=(bl>fl||pc<fl)?bl:fl;if(dir===1&&Q.c[i]<fl)dir=-1;else if(dir===-1&&Q.c[i]>fu)dir=1;}
  pc=Q.c[i];if(dir===1)up[i]=fl;else dn[i]=fu;}
 return[up,dn];}
function psar(Q,st,mx){const n=Q.c.length,o=nul(n);if(n<3)return o;let bull=Q.c[1]>=Q.c[0],sar=bull?Q.l[0]:Q.h[0],ep=bull?Q.h[1]:Q.l[1],af=st;o[1]=sar;
 for(let i=2;i<n;i++){sar=sar+af*(ep-sar);
  if(bull){sar=Math.min(sar,Q.l[i-1],Q.l[i-2]);if(Q.l[i]<sar){bull=false;sar=ep;ep=Q.l[i];af=st;}else if(Q.h[i]>ep){ep=Q.h[i];af=Math.min(af+st,mx);}}
  else{sar=Math.max(sar,Q.h[i-1],Q.h[i-2]);if(Q.h[i]>sar){bull=true;sar=ep;ep=Q.h[i];af=st;}else if(Q.l[i]<ep){ep=Q.l[i];af=Math.min(af+st,mx);}}
  o[i]=sar;}
 return o;}
const sub2=(a,b)=>a.map((v,i)=>v==null||b[i]==null?null:v-b[i]);

/* ---- registry: D(key, name, kind, params[[label,default,step]], lines[[name,colour,style]], fn(Q,p) -> one array per line, extras) ---- */
const IND={};
function D(key,name,kind,params,lines,fn,o){IND[key]=Object.assign({key,name,kind,params,lines,fn},o||{});}
// overlays on the price chart
D('sma','SMA','ov',[['Period',20]],[['SMA','#f5c542']],(Q,p)=>[sma(Q.c,p[0])],{tip:'Simple moving average'});
D('ema','EMA','ov',[['Period',9]],[['EMA','#ff9800']],(Q,p)=>[ema(Q.c,p[0])],{tip:'Exponential moving average'});
D('wma','WMA','ov',[['Period',20]],[['WMA','#8bc34a']],(Q,p)=>[wma(Q.c,p[0])],{tip:'Weighted moving average'});
D('bb','Bollinger Bands','ov',[['Period',20],['StdDev',2,0.5]],[['Upper','#4aa3ff'],['Basis','#f5c542'],['Lower','#4aa3ff']],
  (Q,p)=>{const m=sma(Q.c,p[0]),s=stdev(Q.c,p[0]);return[m.map((v,i)=>v==null?null:v+p[1]*s[i]),m,m.map((v,i)=>v==null?null:v-p[1]*s[i])];});
D('vwap','VWAP (resets daily)','ov',[],[['VWAP','#e91e63']],Q=>[vwap(Q)],{needV:1,tip:'Needs volume'});
D('st','Supertrend','ov',[['ATR period',10],['Multiplier',3,0.5]],[['Up','#26a69a'],['Down','#ef5350']],(Q,p)=>supertrend(Q,p[0],p[1]));
D('psar','Parabolic SAR','dots',[['Step',0.02,0.01],['Max',0.2,0.05]],[['Below price','#26a69a'],['Above price','#ef5350']],(Q,p)=>[psar(Q,p[0],p[1])]);
D('dc','Donchian Channel','ov',[['Period',20]],[['Upper','#42a5f5'],['Middle','#9e9e9e'],['Lower','#42a5f5']],
  (Q,p)=>{const u=rmax(Q.h,p[0]),l=rmin(Q.l,p[0]);return[u,u.map((v,i)=>v==null?null:(v+l[i])/2),l];});
D('kc','Keltner Channel','ov',[['EMA period',20],['ATR period',10],['Multiplier',2,0.5]],[['Upper','#ab47bc'],['Middle','#ab47bc'],['Lower','#ab47bc']],
  (Q,p)=>{const m=ema(Q.c,p[0]),a=rma(trng(Q),p[1]);return[m.map((v,i)=>v==null||a[i]==null?null:v+p[2]*a[i]),m,m.map((v,i)=>v==null||a[i]==null?null:v-p[2]*a[i])];});
// volume (histogram at the bottom of the price chart)
D('vol','Volume','vol',[['MA period',20]],[['Volume','#26a69a','vol'],['MA','#f5c542']],(Q,p)=>[Q.v.slice(),p[0]>0?sma(Q.v,p[0]):nul(Q.c.length)],{needV:1,tip:'Traded volume'});
// separate panes
D('rsi','RSI','osc',[['Period',14]],[['RSI','#a78bfa']],(Q,p)=>[rsi(Q.c,p[0])],{refs:[30,70],range:[0,100],tip:'Relative Strength Index'});
D('macd','MACD','osc',[['Fast',12],['Slow',26],['Signal',9]],[['MACD','#2962ff'],['Signal','#ff6d00'],['Hist','#26a69a','hist']],
  (Q,p)=>{const m=sub2(ema(Q.c,p[0]),ema(Q.c,p[1])),s=ema(m,p[2]);return[m,s,sub2(m,s)];},{refs:[0]});
D('stoch','Stochastic','osc',[['%K',14],['Smooth',3],['%D',3]],[['%K','#2962ff'],['%D','#ff6d00']],
  (Q,p)=>{const k=sma(stochRaw(Q.c,Q.h,Q.l,p[0]),p[1]);return[k,sma(k,p[2])];},{refs:[20,80],range:[0,100]});
D('srsi','Stochastic RSI','osc',[['RSI',14],['Stoch',14],['%K',3],['%D',3]],[['%K','#2962ff'],['%D','#ff6d00']],
  (Q,p)=>{const r=rsi(Q.c,p[0]),hh=rmax(r,p[1]),ll=rmin(r,p[1]),raw=r.map((x,i)=>hh[i]==null?null:(hh[i]===ll[i]?50:(x-ll[i])/(hh[i]-ll[i])*100)),k=sma(raw,p[2]);return[k,sma(k,p[3])];},{refs:[20,80],range:[0,100]});
D('cci','CCI','osc',[['Period',20]],[['CCI','#26c6da']],(Q,p)=>[cci(Q,p[0])],{refs:[-100,0,100]});
D('wr','Williams %R','osc',[['Period',14]],[['%R','#ec407a']],(Q,p)=>{const hh=rmax(Q.h,p[0]),ll=rmin(Q.l,p[0]);return[Q.c.map((x,i)=>hh[i]==null?null:(hh[i]===ll[i]?-50:-100*(hh[i]-x)/(hh[i]-ll[i])))];},{refs:[-20,-80],range:[-100,0]});
D('roc','ROC','osc',[['Period',9]],[['ROC %','#ffa726']],(Q,p)=>[Q.c.map((x,i)=>i>=p[0]&&Q.c[i-p[0]]?(x-Q.c[i-p[0]])/Q.c[i-p[0]]*100:null)],{refs:[0]});
D('mom','Momentum','osc',[['Period',10]],[['MOM','#7e57c2']],(Q,p)=>[Q.c.map((x,i)=>i>=p[0]?x-Q.c[i-p[0]]:null)],{refs:[0]});
D('ao','Awesome Oscillator','osc',[],[['AO','#26a69a','hist']],Q=>{const m=Q.h.map((h,i)=>(h+Q.l[i])/2);return[sub2(sma(m,5),sma(m,34))];},{refs:[0]});
D('adx','ADX / DMI','osc',[['Period',14]],[['ADX','#f5c542'],['+DI','#26a69a'],['-DI','#ef5350']],(Q,p)=>adx(Q,p[0]),{refs:[25]});
D('aroon','Aroon','osc',[['Period',25]],[['Up','#26a69a'],['Down','#ef5350']],(Q,p)=>aroon(Q,p[0]),{refs:[50],range:[0,100]});
D('atr','ATR','osc',[['Period',14]],[['ATR','#ff7043']],(Q,p)=>[rma(trng(Q),p[0])]);
D('mfi','MFI','osc',[['Period',14]],[['MFI','#66bb6a']],(Q,p)=>[mfi(Q,p[0])],{needV:1,refs:[20,80],range:[0,100],tip:'Money Flow Index (needs volume)'});
D('obv','OBV','osc',[],[['OBV','#42a5f5']],Q=>[obv(Q)],{needV:1,tip:'On-Balance Volume (needs volume)'});
const CATS=[['Overlays on price',['sma','ema','wma','bb','vwap','st','psar','dc','kc']],['Volume',['vol','obv','mfi']],
 ['Momentum',['rsi','macd','stoch','srsi','cci','wr','roc','mom','ao']],['Trend strength / volatility',['adx','aroon','atr']]];
function compute(ind,Q){const d=IND[ind.key],n=Q.c.length;
 if(d.needV&&!Q.hv)return{data:d.lines.map(()=>nul(n)),warn:'needs volume: not available on the index/spread, pick an option leg'};
 try{return{data:d.fn(Q,ind.p.map(Number))};}catch(e){return{data:d.lines.map(()=>nul(n)),warn:'calc error'};}}

/* ================= INDICATORS: state, panes, dialog ================= */
let inds=[],R=[],idc=0;
function mkInd(key){const d=IND[key];return{id:'i'+(++idc)+'_'+Math.random().toString(36).slice(2,6),key,on:true,p:d.params.map(x=>x[1]),c:d.lines.map(x=>x[1])};}
try{inds=JSON.parse(localStorage.getItem('inds2')||'null');}catch(e){inds=null;}
if(!Array.isArray(inds))inds=[mkInd('vol'),mkInd('rsi')];
inds=inds.filter(i=>i&&IND[i.key]).map(i=>{const d=IND[i.key];if(!i.id)i.id='i'+(++idc)+'_'+Math.random().toString(36).slice(2,6);
 if(!Array.isArray(i.p)||i.p.length!==d.params.length)i.p=d.params.map(x=>x[1]);if(!Array.isArray(i.c)||i.c.length!==d.lines.length)i.c=d.lines.map(x=>x[1]);return i;});
const saveInds=()=>{try{localStorage.setItem('inds2',JSON.stringify(inds));}catch(e){}};
const indTitle=i=>IND[i.key].name+(i.p.length?' '+i.p.join(', '):'');
const isH=l=>l[2]==='hist'||l[2]==='vol'?1:0;
function ctxBars(){const t=[],o=[],h=[],l=[],c=[],v=[];for(const b of bars){t.push(b.time);o.push(b.open);h.push(b.high);l.push(b.low);c.push(b.close);v.push(b.volume||0);}return{t,o,h,l,c,v,hv:v.some(x=>x>0)};}
function hcol(st,arr,i){if(st==='vol')return bars[i]&&bars[i].close>=bars[i].open?'rgba(38,166,154,.55)':'rgba(239,83,80,.55)';
 const v=arr[i],pv=i?arr[i-1]:null;if(v>=0)return pv==null||v>=pv?'#26a69a':'#9ad6cf';return pv==null||v<=pv?'#ef5350':'#f5aaa8';}
function mkPt(arr,st,i){const v=arr&&arr[i],tm=bars[i].time;if(v==null||!isFinite(v))return{time:tm};return st?{time:tm,value:v,color:hcol(st,arr,i)}:{time:tm,value:v};}
const mkData=(arr,st)=>bars.map((b,i)=>mkPt(arr,st,i));
const LWopts=vis=>({autoSize:true,layout:{background:{color:'#0a0e14'},textColor:'#aab6c3'},grid:{vertLines:{color:'#151c24'},horzLines:{color:'#151c24'}},crosshair:{mode:0},
 rightPriceScale:{borderColor:'#26303b',minimumWidth:72},timeScale:{borderColor:'#26303b',timeVisible:true,secondsVisible:false,rightOffset:8,visible:vis}});
chart.applyOptions({rightPriceScale:{minimumWidth:72}});

let syncing=false;
function syncRange(src,rg){if(syncing||!rg)return;syncing=true;try{[chart,...R.filter(x=>x.sub).map(x=>x.chart)].forEach(c=>{if(c!==src)c.timeScale().setVisibleLogicalRange(rg);});}catch(e){}syncing=false;}
chart.timeScale().subscribeVisibleLogicalRangeChange(rg=>syncRange(chart,rg));

function hdrHtml(i){const id=i.id;return `<b style="color:${(i.c&&i.c[0])||'#ccc'}">${indTitle(i)}</b><span class="iv" data-id="${id}"></span>`+
 `<button data-a="eye" data-id="${id}" title="Show / hide">👁</button><button data-a="cfg" data-id="${id}" title="Settings">⚙</button><button data-a="del" data-id="${id}" title="Remove">✕</button>`;}
function renderChips(){const bar=$('ovbar'),ov=inds.filter(i=>IND[i.key].kind!=='osc');bar.style.display=ov.length?'flex':'none';
 bar.innerHTML=ov.map(i=>`<span class="ichip${i.on?'':' off'}">${hdrHtml(i)}</span>`).join('');}
function clearInds(){R.forEach(r=>{try{if(r.sub){r.chart.remove();r.el.remove();}else r.series.forEach(s=>{try{chart.removeSeries(s);}catch(e){}});}catch(e){}});R=[];$('subs').innerHTML='';}

function applyInds(){
 clearInds();const Q=ctxBars();let hasVol=false;
 for(const ind of inds){
  if(!ind.on)continue;const d=IND[ind.key],res=compute(ind,Q),sub=d.kind==='osc';
  const r={ind,d,data:res.data,warn:res.warn,sub,series:[],n:bars.length};
  if(d.kind==='dots'){r.dots=true;R.push(r);continue;}
  let ch=chart;
  if(sub){const el=document.createElement('div');el.className='sub';el.innerHTML=`<div class="cn"></div><div class="phd">${hdrHtml(ind)}</div>`;$('subs').appendChild(el);
   ch=LightweightCharts.createChart(el.querySelector('.cn'),LWopts(false));r.chart=ch;r.el=el;
   ch.timeScale().subscribeVisibleLogicalRangeChange(rg=>syncRange(ch,rg));ch.subscribeCrosshairMove(p=>showVals(p.time));}
  const order=d.lines.map((_,i)=>i).sort((a,b)=>isH(d.lines[b])-isH(d.lines[a]));r.series=new Array(d.lines.length);
  for(const i of order){
   const ln=d.lines[i],st=ln[2],hist=(st==='vol'||st==='hist')?st:null;let s;
   if(st==='vol'){hasVol=true;s=ch.addHistogramSeries({priceScaleId:'vol',priceFormat:{type:'volume'},priceLineVisible:false,lastValueVisible:false});ch.priceScale('vol').applyOptions({scaleMargins:{top:.8,bottom:0}});}
   else if(st==='hist')s=ch.addHistogramSeries({priceLineVisible:false,lastValueVisible:false});
   else{const o={color:ind.c[i]||ln[1],lineWidth:(d.lines.length===1||ind.key==='st')?2:1,priceLineVisible:false,lastValueVisible:sub,crosshairMarkerVisible:false};
    if(d.kind==='vol')o.priceScaleId='vol';
    if(d.range&&i===0)o.autoscaleInfoProvider=()=>({priceRange:{minValue:d.range[0],maxValue:d.range[1]}});
    s=ch.addLineSeries(o);}
   s.setData(mkData(res.data[i],hist));r.series[i]=s;}
  if(sub&&d.refs)d.refs.forEach(v=>{try{r.series[0].createPriceLine({price:v,color:'#3d4a5a',lineWidth:1,lineStyle:2,axisLabelVisible:false,title:''});}catch(e){}});
  R.push(r);}
 chart.priceScale('right').applyOptions({scaleMargins:hasVol?{top:.1,bottom:.24}:{top:.2,bottom:.1}});
 const subs=R.filter(r=>r.sub);chart.applyOptions({timeScale:{visible:!subs.length}});
 subs.forEach((r,i)=>r.chart.applyOptions({timeScale:{visible:i===subs.length-1}}));
 const rg=chart.timeScale().getVisibleLogicalRange();if(rg)subs.forEach(r=>{try{r.chart.timeScale().setVisibleLogicalRange(rg);}catch(e){}});
 renderChips();showVals();
}
function refreshInds(app){
 if(!R.length)return;const Q=ctxBars(),n=bars.length;if(!n)return;
 for(const r of R){const res=compute(r.ind,Q);r.data=res.data;r.warn=res.warn;if(r.dots)continue;
  for(let k=0;k<r.series.length;k++){const st=r.d.lines[k][2],hist=(st==='vol'||st==='hist')?st:null,s=r.series[k];if(!s)continue;
   try{if(app||r.n!==n)s.setData(mkData(r.data[k],hist));else s.update(mkPt(r.data[k],hist,n-1));}catch(e){try{s.setData(mkData(r.data[k],hist));}catch(e2){}}}
  r.n=n;}
 showVals();
}
function idxAt(tm){let hi=bars.length-1,lo=0;if(hi<0)return -1;if(tm==null)return hi;while(lo<hi){const m=(lo+hi+1)>>1;if(bars[m].time<=tm)lo=m;else hi=m-1;}return lo;}
function fv(v,st){if(v==null)return '–';const a=Math.abs(v);
 if(st==='vol'||a>=1e5)return a>=1e7?(v/1e7).toFixed(2)+'Cr':a>=1e5?(v/1e5).toFixed(2)+'L':a>=1e3?(v/1e3).toFixed(1)+'K':String(Math.round(v));
 return fx(v,a<10?3:2);}
function showVals(tm){const i=idxAt(tm);if(i<0)return;
 R.forEach(r=>{const el=document.querySelector('.iv[data-id="'+r.ind.id+'"]');if(!el)return;
  if(r.warn){el.innerHTML=`<span style="color:#f5a623">⚠ ${r.warn}</span>`;return;}
  el.innerHTML=r.d.lines.map((ln,k)=>{const v=r.data[k]?r.data[k][i]:null;return `<span style="color:${isH(ln)?'#9fb0c2':(r.ind.c[k]||ln[1])}">${fv(v,ln[2])}</span>`;}).join(' ');});}
function drawDots(){const rg=chart.timeScale().getVisibleLogicalRange();if(!rg)return;
 for(const r of R){if(!r.dots)continue;const a=r.data[0];if(!a)continue;const i0=Math.max(0,Math.floor(rg.from)-1),i1=Math.min(bars.length-1,Math.ceil(rg.to)+1);
  ctx.save();for(let i=i0;i<=i1;i++){const v=a[i];if(v==null)continue;const x=chart.timeScale().logicalToCoordinate(i),y=Y(v);if(x==null||y==null)continue;
   ctx.fillStyle=v<bars[i].close?(r.ind.c[0]||'#26a69a'):(r.ind.c[1]||'#ef5350');ctx.beginPath();ctx.arc(x,y,2.2,0,7);ctx.fill();}ctx.restore();}}

/* ---- dialog ---- */
function buildDlg(){const q=$('indq').value.trim().toLowerCase();
 $('indlist').innerHTML=CATS.map(([t,ks])=>{const f=ks.filter(k=>!q||IND[k].name.toLowerCase().includes(q)||k.includes(q));
  return f.length?`<h4>${t}</h4>`+f.map(k=>`<button class="addind" data-k="${k}" title="${IND[k].tip||''}">${IND[k].name}${IND[k].needV?' <small style="opacity:.6">(needs volume)</small>':''}</button>`).join(''):'';}).join('')||'<span style="opacity:.6">no match</span>';
 buildAct();}
function buildAct(){$('indact').innerHTML=inds.length?inds.map(i=>{const d=IND[i.key];
  return `<div class="arow" data-id="${i.id}"><label><input type="checkbox" data-f="on" ${i.on?'checked':''}> <b>${d.name}</b></label>`+
   d.params.map((p,k)=>`<span>${p[0]} <input type="number" data-f="p" data-k="${k}" value="${i.p[k]}" step="${p[2]||1}" min="${p[2]&&p[2]<1?0.001:1}"></span>`).join('')+
   d.lines.map((l,k)=>isH(l)?'':`<input type="color" data-f="c" data-k="${k}" value="${i.c[k]||l[1]}" title="${l[0]}">`).join('')+
   `<button data-a="del" data-id="${i.id}" title="Remove">✕</button></div>`;}).join('')+'<p><button id="indclr">Remove all</button></p>':'<span style="opacity:.6">Nothing added yet. Click an indicator on the left.</span>';}
const openDlg=()=>{$('indDlg').style.display='flex';buildDlg();$('indq').focus();},closeDlg=()=>{$('indDlg').style.display='none';};
$('bInd').onclick=openDlg;$('indx').onclick=closeDlg;$('indq').oninput=buildDlg;
$('indlist').addEventListener('click',e=>{const b=e.target.closest('.addind');if(!b)return;inds.push(mkInd(b.dataset.k));saveInds();applyInds();buildAct();});
$('indact').addEventListener('click',e=>{if(e.target.id==='indclr'){inds=[];saveInds();applyInds();buildAct();}});
$('indact').addEventListener('change',e=>{const t=e.target,row=t.closest('.arow');if(!row)return;const ind=inds.find(i=>i.id===row.dataset.id);if(!ind)return;const f=t.dataset.f;
 if(f==='on')ind.on=t.checked;
 else if(f==='p'){const k=+t.dataset.k,d=IND[ind.key].params[k],isInt=!(d[2]&&d[2]<1);let v=parseFloat(t.value);if(!isFinite(v)||v<=0)v=d[1];if(isInt)v=Math.max(1,Math.round(v));ind.p[k]=v;t.value=v;}
 else if(f==='c')ind.c[+t.dataset.k]=t.value;
 saveInds();applyInds();});
document.addEventListener('click',e=>{const b=e.target.closest('[data-a]');if(!b)return;const i=inds.find(x=>x.id===b.dataset.id);if(!i)return;const a=b.dataset.a;
 if(a==='cfg'){openDlg();return;}if(a==='eye')i.on=!i.on;else if(a==='del')inds=inds.filter(x=>x!==i);
 saveInds();applyInds();if($('indDlg').style.display==='flex')buildAct();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDlg();
 if(e.key.toLowerCase()==='i'&&!e.ctrlKey&&!e.metaKey&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName))openDlg();});

/* ---------- P&L chart ---------- */
const pc=LightweightCharts.createChart($('pnlchart'),{autoSize:true,layout:{background:{color:'#0a0e14'},textColor:'#aab6c3'},grid:{vertLines:{color:'#151c24'},horzLines:{color:'#151c24'}},
 rightPriceScale:{borderColor:'#26303b'},timeScale:{borderColor:'#26303b',timeVisible:true,secondsVisible:true}});
const pl=pc.addLineSeries({color:'#378ADD',lineWidth:2,title:'Net P&L'});let plines=[],plk='';
function updPnl(){const h=(S&&S.hist)||[];pl.setData(h.map(([t,v])=>({time:t,value:v})));
 const k=S&&S.pnl?`${S.pnl.target}|${S.pnl.buffer}`:'';if(k!==plk){plk=k;plines.forEach(l=>pl.removePriceLine(l));plines=[];
  if(S&&S.pnl){plines=[pl.createPriceLine({price:S.pnl.target,color:'#26a69a',lineStyle:2,title:'target'}),pl.createPriceLine({price:-S.pnl.buffer,color:'#ef5350',lineStyle:2,title:'stop'}),pl.createPriceLine({price:0,color:'#888',lineStyle:3,title:''})];}}}

/* ---------- panel ---------- */
const card=(l,v,s='')=>`<div class="card"><div class="l">${l}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
let setBuilt=false,sqArm=0;
const LB={LOTS:['How many lots','One lot = one standard Nifty lot.'],
 SHORT_MODE:['How to pick the put you sell','By delta = by how far it is from the price. At the money = the strike closest to the price.'],
 SHORT_DELTA:['Delta of the put you sell','Smaller = further from the price, safer, but you collect less. 0.30 is common.'],
 HEDGE_MODE:['How to pick the put you buy (your protection)','By delta, or a fixed number of points below the put you sell.'],
 HEDGE_DELTA:['Delta of the protection put','Smaller = cheaper protection but more risk.'],
 HEDGE_POINTS:['Points below the put you sell','Only used when protection is picked by points.'],
 TARGET_PCT:['Take profit at (% of the money you collect)','30 = close the trade once you have earned 30% of what you collected.'],
 BUFFER_LOSS_X:['Stop loss (times the money you collect)','1 = close the trade when the loss equals what you collected.'],
 BUFFER_SHORT_DELTA:['Also close if the put you sold gets this risky (delta)','Gets you out early if the market runs towards your strike.'],
 ENTRY_TIME:['Enter at','Type now to enter as soon as possible, or a time like 09:30.'],ENTRY_CUTOFF:['No new trades after','A time like 15:00.'],
 EXIT_TIME:['Close every trade at','A time like 15:15.'],INTRADAY_ONLY:['Strictly same-day trading','Closes every trade on the same day at the time above.'],
 MAX_TRADES_PER_DAY:['Most trades in one day',''],REENTER_AFTER_RULE_EXIT:['Trade again after a profit or stop-loss exit','If off, only a manual exit leads to a new trade (when Auto re-enter is on).'],
 REENTER_COOLDOWN_SEC:['Wait before trading again (seconds)',''],SLIP_FALLBACK:['Price cushion when no bid/ask is available (points)','Keeps the practice fills careful when live bid/ask is missing.'],
 MAX_HOLD_DAYS:['Longest to hold a trade (days)','Only used when same-day trading is off.'],MIN_DTE_EXIT:['Close this many days before expiry','Only used when same-day trading is off.'],
 SC_MIN_MOVE:['Scorecard: smallest move that counts (points)','After a signal, a move smaller than this is ignored: it is neither right nor wrong. 8 is a sensible start for Nifty. Raise it to be stricter.'],SHOW_SR_LINES:['Show support and resistance lines on the chart','Orange = nearest resistance, blue = nearest support (Nifty chart).'],SHOW_DMA_LINES:['Show daily moving average lines on the chart','The 20, 50 and 200 day moving averages on the Nifty chart.'],RECORD_TICKS:['Record Nifty ticks to files','Nifty spot, Nifty futures and the option legs of your trades (not the heavyweight stocks). Saved in spread_data/ticks, one file per day, for replay and backtesting.'],BACKTEST_MODE:['Backtest mode: replay a past day on the chart','Switches the chart to replay and blocks new live trades. Pick a date and press Play on the chart bar. Turn it off to go back to live.'],
 FUT_LOTS:['Lots per futures trade',''],FUT_TARGET_PTS:['Take profit at (points)','Points gained on the futures price.'],FUT_STOP_PTS:['Stop loss at (points)','Points lost on the futures price.'],PARTIAL_ON:['Use automatic partial exit','When a trade reaches part of its target, close some of it and keep the rest running.'],PARTIAL_AT_PCT:['Take the partial when profit reaches (% of the target)','50 = when you are halfway to the profit target.'],PARTIAL_CLOSE_PCT:['Close this much of the trade (%)','50 = half of the lots. The trade needs at least 2 lots.'],PARTIAL_MOVE_STOP:['Then protect the rest at breakeven','If the part that is left falls back to zero profit, close it.'],
 TRADE_MODE:['What the buttons trade','Options = spreads and single options. Futures = the NIFTY futures contract, like a real futures account.'],NK_LOTS:['Lots for a direct buy / sell',''],NK_OPT:['Option type for a direct trade (options mode)','Call or put. BUY = you own it, SELL = you write it.'],NK_STRIKE_MODE:['Which strike (options mode)','At the money, or by delta.'],NK_DELTA:['Delta when picking by delta','0.30 is a little out of the money.'],NK_TARGET_PTS:['Take profit at (points)','Points gained on the option or futures price.'],NK_STOP_PTS:['Stop loss at (points)','Points lost on the option or futures price.'],FUT_MARGIN_PCT:['Futures margin (% of contract value)','Practice-money margin held for each futures trade.'],
 SIG_VWAP_BUFFER:['VWAP signal: how far beyond VWAP (points)','Price must be at least this many points above VWAP to say bullish, or below VWAP to say bearish. Raise it if the signal flips too often.'],DMA_FAST:['Fast daily moving average (days)','Usually 20.'],DMA_SLOW:['Slow daily moving average (days)','Usually 50.'],DMA_LONG:['Long daily moving average (days)','Usually 200.'],
 SIG_RVOL_MIN:['Volume must be at least this many times the usual','Used by the relative volume signal. 1.5 = 50% more than usual.'],
 SIG_SLOPE_MIN:['VWAP direction is measured over (minutes)','Used by the VWAP signal.'],SR_TOL_PCT:['How close turning points must be to count as one level (% of price)','0.08 = within about 20 points on Nifty.'],MAX_OPEN_TRADES:['Most trades open at the same time','Each time you press an Enter button a new trade is added.'],SIG_MAX_PER_SIDE:['Most signal-driven trades open per side','1 = at most one bull put and one bear call from the signals at a time.'],SIG_OI_BUFFER:['OI wall break: close must pass the wall by (points)',''],SIG_CONTRIB_MIN:['Index contribution: heavyweights must add at least (points)','Used by the index point contribution signal.'],SIG_COOLDOWN_MIN:['Wait between signal trades (minutes)','Stops the system from trading again straight away.']};
['LOTS','SHORT_MODE','SHORT_DELTA','HEDGE_MODE','HEDGE_DELTA','HEDGE_POINTS','TARGET_PCT','BUFFER_LOSS_X','BUFFER_SHORT_DELTA'].forEach(k=>{
 LB['CALL_'+k]=[LB[k][0].replace(/put/g,'call'),LB[k][1].replace(/put/g,'call').replace('below','above')];});
const OPT={options:'Options (spreads and single options)',futures:'Futures (the real NIFTY futures contract)',CE:'Call (CE)',PE:'Put (PE)',delta:'By delta',atm:'At the money',points:'Fixed points away'};
const BASE=['LOTS','SHORT_MODE','SHORT_DELTA','HEDGE_MODE','HEDGE_DELTA','HEDGE_POINTS','TARGET_PCT','BUFFER_LOSS_X','BUFFER_SHORT_DELTA'];
let subset=(()=>{try{return localStorage.getItem('subset')||'general';}catch(e){return 'general';}})();
const SETGROUPS={general:{title:'General',note:'What the buttons trade, timing and limits that apply to everything.',keys:['TRADE_MODE','MAX_OPEN_TRADES','ENTRY_TIME','ENTRY_CUTOFF','EXIT_TIME','INTRADAY_ONLY','MAX_TRADES_PER_DAY','REENTER_AFTER_RULE_EXIT','REENTER_COOLDOWN_SEC','SLIP_FALLBACK','MAX_HOLD_DAYS','MIN_DTE_EXIT','#Chart lines, data recording and backtest','SHOW_SR_LINES','SHOW_DMA_LINES','RECORD_TICKS','BACKTEST_MODE','#Signal scorecard','SC_MIN_MOVE']},
 options:{title:'Options (spreads)',note:'Used when What the buttons trade is set to Options (General tab).',keys:['#Bull put spread: you sell a put and buy a cheaper one below it. Used when you expect the market to hold or rise.',...BASE,'#Bear call spread: you sell a call and buy a cheaper one above it. Used when you expect the market to fall or stay down.',...BASE.map(k=>'CALL_'+k)]},
 futures:{title:'Futures',note:'Used when What the buttons trade is set to Futures. BUY and SELL trade the NIFTY futures contract.',keys:['FUT_LOTS','FUT_TARGET_PTS','FUT_STOP_PTS','FUT_MARGIN_PCT']},
 direct:{title:'Direct buy / sell (single option)',note:'Used by the BUY and SELL buttons in Options mode: one call or put with no strategy.',keys:['NK_LOTS','NK_OPT','NK_STRIKE_MODE','NK_DELTA','NK_TARGET_PTS','NK_STOP_PTS']},
 partial:{title:'Partial exits',note:'Take some profit early and let the rest run. You can also close part of any trade yourself with the Partial button in the trades list.',keys:['PARTIAL_ON','PARTIAL_AT_PCT','PARTIAL_CLOSE_PCT','PARTIAL_MOVE_STOP']},
 signals:{title:'Signals',note:'Every signal has its own section: whether it counts, how often it is checked, and its numbers. "How often it is checked" is in seconds. On the VWAP signal, 0 means it is checked on every single futures tick. The other signals are checked every few seconds.',keys:[
 '#How the signals are combined and traded','SIG_COMBINE','SIG_AUTO_TRADE','SIG_EXIT_ON_OPPOSITE','SIG_NOTIFY','SIG_MAX_PER_SIDE','SIG_COOLDOWN_MIN',
 '#1. Futures VWAP + slope + distance','SIG_USE_VWAP','SIG_RF_VWAP','SIG_SLOPE_MIN','SIG_VWAP_BUFFER','SIG_VWAP_COMPRESS',
 '#2. Futures relative volume','SIG_USE_RVOL','SIG_RF_RVOL','SIG_RVOL_MIN',
 '#3. Opening-range breakout','SIG_USE_ORB','SIG_RF_ORB',
 '#4. Price structure','SIG_USE_STRUCT','SIG_RF_STRUCT',
 '#5. EMA 9 / EMA 21','SIG_USE_EMA','SIG_RF_EMA',
 '#6. OI wall break','SIG_USE_OI','SIG_RF_OI','SIG_OI_BUFFER',
 '#7. Index point contribution','SIG_USE_CONTRIB','SIG_RF_CONTRIB','SIG_CONTRIB_MIN',
 '#8. Delta / footprint divergence','SIG_USE_DELTA','SIG_RF_DELTA','SIG_DELTA_LOOKBACK','SIG_DELTA_MIN_BARS','SIG_DELTA_FRESH',
 '#9. Liquidity sweep + rejection','SIG_USE_SWEEP','SIG_RF_SWEEP','SIG_SWEEP_LOOKBACK','SIG_SWEEP_CLOSE_FRAC','SIG_SWEEP_MIN_PTS','SIG_SWEEP_MIN_DEPTH',
 '#10. VWAP bands + slope','SIG_USE_VWBAND','SIG_RF_VWBAND','SIG_VWB_MIN_BARS','SIG_VWB_MIN_SIGMA','SIG_VWB_EXT','SIG_VWB_SLOPE_MIN','SIG_VWB_FLAT','SIG_VWB_WINDOW',
 '#11. Opening range + volume','SIG_USE_ORBV','SIG_RF_ORBV',
 '#12. Cumulative delta trend','SIG_USE_DTREND','SIG_RF_DTREND','SIG_DT_BARS','SIG_DT_MIN_RATIO',
 '#13. DMA trend (daily moving averages)','SIG_USE_DMA','SIG_RF_DMA','DMA_FAST','DMA_SLOW','DMA_LONG',
 '#14. VWAP first flip (after a quiet spell)','SIG_USE_VWX','SIG_RF_VWX','SIG_VWX_QUIET','SIG_VWX_HOLD',
 '#Support and resistance levels','SR_TOL_PCT']}};
const SIGN=['VWAP','RVOL','ORB','STRUCT','EMA','OI','CONTRIB','DELTA','SWEEP','VWBAND','ORBV','DTREND','DMA','VWX'];
SIGN.forEach(k=>{LB['SIG_USE_'+k]=['Use this signal in the overall view','Tick = it counts towards the overall view and automatic trading.'];
 LB['SIG_RF_'+k]=k==='VWAP'?['How often it is checked (seconds)','0 = on every single futures tick, the most sensitive. Any other number = checked every that many seconds.']:['How often it is checked (seconds)','Seconds between checks. 5 is the usual. Lower reacts faster but can flip more.'];});
Object.assign(LB,{SIG_COMBINE:['How the ticked signals are combined','All must agree, or at least one with none against, or most of them.'],SIG_AUTO_TRADE:['Trade automatically from the overall view (practice money)','Bullish = bull put spread (or buy futures). Bearish = bear call spread (or sell futures).'],
 SIG_EXIT_ON_OPPOSITE:['Close my trade if the signals turn against it',''],SIG_NOTIFY:['Alert me whenever a signal changes',''],
 SIG_VWX_QUIET:['VWAP first flip: quiet minutes before','Signal 1 must not have turned the same way for this many minutes, otherwise the flip is a repeat and ignored. 10 scored best in the research data.'],SIG_VWX_HOLD:['VWAP first flip: minutes the bias is held','15 = the horizon where it worked in the research data. A first flip the other way ends it sooner.'],
 SIG_VWAP_COMPRESS:['VWAP signal: call it "drifting back" below this fraction','The gap to VWAP may shrink to this fraction of before (0.8 = 80%) before the signal is held back.'],
 SIG_DELTA_LOOKBACK:['Delta divergence: minutes looked at',''],SIG_DELTA_MIN_BARS:['Delta divergence: minutes of data needed',''],SIG_DELTA_FRESH:['Delta divergence: how recent the second swing must be (minutes)',''],
 SIG_SWEEP_LOOKBACK:['Sweep: minutes of old highs and lows looked at',''],SIG_SWEEP_CLOSE_FRAC:['Sweep: candle must close in the top or bottom part (fraction)','0.6 = it must close in the top (or bottom) 40% of the candle.'],
 SIG_SWEEP_MIN_PTS:['Sweep: smallest candle size (points)',''],SIG_SWEEP_MIN_DEPTH:['Sweep: wick must go past the old high or low by (points)',''],
 SIG_VWB_MIN_BARS:['VWAP bands: minutes of data needed',''],SIG_VWB_MIN_SIGMA:['VWAP bands: smallest band width (points)','Below this the bands are too narrow to trust.'],SIG_VWB_EXT:['VWAP bands: outer band size (sigma)','2 = two standard deviations from VWAP.'],
 SIG_VWB_SLOPE_MIN:['VWAP bands: slope window (minutes)',''],SIG_VWB_FLAT:['VWAP bands: counts as flat within (points)',''],SIG_VWB_WINDOW:['VWAP bands: look-back for the dip or spike (minutes)',''],
 SIG_DT_BARS:['Delta trend: 5-minute candles looked at (even number)',''],SIG_DT_MIN_RATIO:['Delta trend: net delta needed (fraction of volume)','0.02 = 2%.'],SR_TOL_PCT:['How close turning points must be to count as one level (% of price)','0.08 = within about 20 points on Nifty.']});
Object.assign(OPT,{all:'All ticked signals agree',any:'At least one agrees and none disagree',majority:'Most of the ticked signals agree'});
const SUBS=[['general','General'],['options','Options'],['futures','Futures'],['direct','Direct'],['signals','Signals'],['partial','Partial exits']];
function setSub(k){subset=k;try{localStorage.setItem('subset',k);}catch(e){}buildSettings();}
function buildSettings(){if(!S||!S.settings||view!=='settings')return;const g=SETGROUPS[subset]||SETGROUPS.general;setBuilt=true;
 $('subnav').innerHTML=SUBS.map(([k,t])=>`<button class="${k===subset?'act':''}" onclick="setSub('${k}')">${t}</button>`).join('');$('setTitle').textContent=g.title;$('setNote').textContent=g.note;
 $('settings').innerHTML=g.keys.map(k=>{if(k[0]==='#')return `<h4 style="margin:20px 0 2px">${k.slice(1)}</h4>`;const x=S.settings.find(y=>y.name===k);if(!x)return'';const lb=LB[k]||[k,''];
  const inp=x.type==='select'?`<select data-k="${k}">${x.options.map(o=>`<option value="${o}" ${o===x.value?'selected':''}>${OPT[o]||o}</option>`).join('')}</select>`:
   x.type==='bool'?`<input type="checkbox" data-k="${k}" ${x.value?'checked':''}>`:`<input data-k="${k}" value="${x.value}">`;
  return `<div class="srow"><div>${lb[0]}<div class="h">${lb[1]}</div></div><div>${inp}</div></div>`;}).join('');}
async function loadScore(){try{const d=await (await fetch('/api/scorecard')).json();$('scMinMove').textContent=d.min;
 const pc=v=>v==null?'–':v.toFixed(0)+'%',pt=v=>v==null?'–':(v>=0?'+':'')+v.toFixed(1),hc=v=>v==null?'':(v>=55?'up':v<=45?'dn':'');
 $('scoreTable').innerHTML='<table><tr><th>Signal</th><th>Times it fired</th><th>Right after 15 min</th><th>Right after 30 min</th><th>Right after 60 min</th><th>Avg move 30 min</th><th>Avg move 60 min</th></tr>'+d.rows.map(r=>`<tr><td>${r.name}</td><td>${r.n} (${r.bull} up, ${r.bear} down)</td>
  <td class="${hc(r.hit15)}">${pc(r.hit15)} <small>of ${r.n15}</small></td><td class="${hc(r.hit30)}">${pc(r.hit30)} <small>of ${r.n30}</small></td><td class="${hc(r.hit60)}">${pc(r.hit60)} <small>of ${r.n60}</small></td>
  <td class="${r.avg30>=0?'up':'dn'}">${pt(r.avg30)}</td><td class="${r.avg60>=0?'up':'dn'}">${pt(r.avg60)}</td></tr>`).join('')+'</table>';
 const cell=v=>v===undefined?'<span style="color:var(--mut)">waiting…</span>':v==='x'?'<span style="color:var(--mut)">n/a</span>':`<span class="${v>=0?'up':'dn'}">${pt(v)}</span>`;
 $('scoreEvents').innerHTML=d.events.length?'<table><tr><th>Time</th><th>Signal</th><th>Said</th><th>Nifty then</th><th>After 15 min</th><th>After 30 min</th><th>After 60 min</th></tr>'+d.events.map(e=>`<tr><td>${e.time}</td><td>${e.name}</td>
  <td style="color:${e.state==='BULL'?'#22c55e':'#ef4444'}"><b>${e.state==='BULL'?'BULLISH':'BEARISH'}</b></td><td>${fx(e.price,0)}</td><td>${cell(e.res['15'])}</td><td>${cell(e.res['30'])}</td><td>${cell(e.res['60'])}</td></tr>`).join('')+'</table>':'<span style="color:var(--mut)">No signals recorded yet. They are recorded while the market is open.</span>';}catch(e){}}
setInterval(()=>{if(view==='score')loadScore();},10000);
$('scoreClear').onclick=()=>{if(confirm('Delete the whole scorecard history?'))post('/api/scorecard/clear',{}).then(loadScore);};
function saveFine(){const o={};document.querySelectorAll('#sigTop [data-k]').forEach(e=>{o[e.dataset.k]=e.value;});post('/api/settings',o);sigKey='';}
function render(){
 const s=S;if(!s||!s.legs)return;
 const stc={WAITING:'#BA7517',OPEN:'#378ADD',CLOSED:'#555'}[s.status]||'#555';
 $('pst').textContent=s.status;$('pst').style.background=stc;
 const live=s.feed.live&&s.feed.market_open;$('pfeed').textContent=live?'● LIVE':(s.feed.market_open?'feed stale':'market closed');$('pfeed').style.background=live?'#1a9850':(s.feed.market_open?'#d73027':'#666');
 $('exp').textContent=`${s.strategy} · expiry ${s.expiry} (DTE ${s.dte}) · ${s.qty} qty`;$('clock').textContent=s.time+' IST';
 const se=s.session;$('sess').innerHTML=`Today: ${se.n} trades · realized <b class="${cls(se.realized)}">${money(se.realized)}</b> · total <b class="${cls(se.total)}">${money(se.total)}</b>`;
 $('sline').textContent=s.status_line;
 $('bauto').className=s.auto?'on':'';expSel(s);const fm=s.mode==='futures';$('benp').style.display=$('benc').style.display=fm?'none':'';$('bbuy').textContent=fm?'▲ BUY futures':'▲ BUY option';$('bsell').textContent=fm?'▼ SELL futures':'▼ SELL option';['benp','benc','bbuy','bsell'].forEach(id=>{$(id).disabled=!!s.backtest;});if((!!s.backtest)!==BT){BT=!!s.backtest;btSwitch();}$('bauto').textContent='Auto re-enter: '+(s.auto?'ON':'OFF');
 if(sqArm&&Date.now()>sqArm){sqArm=0;$('bsq').textContent='Square off ALL';}
 let c='';const p=s.pnl;
 if(p){c=card('Net P&L (after charges)',`<span class="${cls(p.net)}">${money(p.net)}</span>`,`gross ${money(p.gross)} · charges ₹${Math.round(p.charges)}`)+
  card('Nifty spot',fx(s.spot),`short strike ${s.legs[0].strike} (${s.spot?(s.spot-s.legs[0].strike>=0?'+':'')+(s.spot-s.legs[0].strike).toFixed(0):'–'} pts)`)+
  card('Target',money(p.target),`${p.target_pct.toFixed(0)}% reached`)+card('Buffer (stop)',money(-p.buffer),`${p.room_pct.toFixed(0)}% room left`)+
  card('Credit',fx(p.credit)+' pts','breakeven '+fx(p.breakeven,0))+card('Held',p.held+' / '+'days',p.entry_time);
 }else if(s.plan){const pl_=s.plan;c=card('Nifty spot',fx(s.spot))+card('Planned credit',pl_.credit==null?'–':fx(pl_.credit)+' pts',pl_.credit==null?'':'₹'+Math.round(pl_.credit_rs))+
  card('Width',s.width+' pts','max loss ≈ ₹'+Math.round(pl_.max_loss))+card('Status','WAITING','');}
 $('cards').innerHTML=c;
 $('details').innerHTML=p?'<table>'+[['Trade ID',p.trade_id],['Option expiry',(p.expiry||'–')+(s.dte!=null?'  ('+s.dte+' day'+(s.dte==1?'':'s')+' left)':'')],['Entered',p.entry_time+' @ spot '+fx(p.entry_spot)],['Spot now',fx(s.spot)],
  ['Credit',fx(p.credit)+' pts = ₹'+Math.round(p.max_profit)],['Max profit / max loss','₹'+Math.round(p.max_profit)+' / ₹'+Math.round(p.max_loss)],
  ['Breakeven (expiry)',fx(p.breakeven,0)+(s.spot?'  ('+(s.spot-p.breakeven>=0?'+':'')+(s.spot-p.breakeven).toFixed(0)+' pts away)':'')],
  ['Best / worst P&L so far',money(p.max_pnl||0)+' / '+money(p.min_pnl||0)],['DTE at entry / now',(p.entry_dte==null?'–':p.entry_dte)+' / '+s.dte],
  ['Gross / charges',money(p.gross)+' / ₹'+Math.round(p.charges)]].map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join('')+'</table>':'';
 $('legs').innerHTML='<table><tr><th>Leg</th><th>Entry</th><th>LTP</th><th>Bid</th><th>Ask</th><th>Δ</th><th>θ</th><th>IV</th></tr>'+
  s.legs.map(l=>`<tr><td>${l.role} ${s.instr?s.instr:(l.strike+' '+s.opt)}</td><td>${fx(l.entry)}</td><td>${fx(l.ltp)}</td><td>${fx(l.bid)}</td><td>${fx(l.ask)}</td><td>${fx(l.delta,3)}</td><td>${fx(l.theta)}</td><td>${fx(l.iv,1)}</td></tr>`).join('')+'</table>';
 $('nets').innerHTML=s.nets?card('Net delta',s.nets.delta.toFixed(1),'₹ / Nifty pt')+card('Net theta',money(s.nets.theta),'per day')+card('Net vega',money(s.nets.vega),'per vol pt'):'';
 $('rules').innerHTML=s.rules.length?'<table><tr><th>Exit rule</th><th>Now</th><th>Limit</th><th></th></tr>'+s.rules.map(r=>`<tr><td>${r.name}</td><td>${r.now}</td><td>${r.limit}</td><td class="${r.trig?'dn':'up'}"><b>${r.trig?'TRIGGER':'ok'}</b></td></tr>`).join('')+'</table>':'';
 $('log').textContent=s.log.join('\n');
 if(view==='settings'&&!setBuilt)buildSettings();
 (s.msgs||[]).forEach(m=>{if(m.id>lastToast){lastToast=m.id;const t=document.createElement('div');t.className='toast';t.textContent=m.t+' · '+m.text;if(snd&&/TRIGGER|Closed|Squared|ALERT|ENTRY|Entering|SIGNAL/.test(m.text))beep();if(/SIGNAL/.test(m.text)&&window.Notification&&Notification.permission==='granted')new Notification('NIFTY signal',{body:m.text});$('toasts').appendChild(t);setTimeout(()=>t.remove(),7000);}});
 updPnl();renderTrigs();ribbon();renderSignals();renderPositions();renderAccount();
}
function renderAccount(){const a=S&&S.account;if(!a)return;
 $('eqbig').innerHTML=`<span style="color:var(--mut);font-size:11px">Equity</span> <b class="${cls(a.equity-a.start)}">₹${Math.round(a.equity).toLocaleString('en-IN')}</b>`;
 const el=$('acct');if(!el.dataset.built){el.dataset.built=1;el.innerHTML='<div id="acctc" class="cards"></div><div style="display:flex;gap:8px;align-items:center;margin:0 0 10px;flex-wrap:wrap"><button id="bclear" class="red">🗑 Clear all &amp; start fresh</button><span id="acctnote" style="color:var(--mut);font-size:11px"></span></div>';$('bclear').onclick=clearAll;}
 $('acctc').innerHTML=card('Account equity',`<span class="${cls(a.equity-a.start)}">₹${Math.round(a.equity).toLocaleString('en-IN')}</span>`,`${a.ret_pct>=0?'+':''}${a.ret_pct.toFixed(2)}% vs start`)+
  card('Starting equity','₹'+Math.round(a.start).toLocaleString('en-IN'),'since '+(a.created||'–'))+
  card('Realized P&L (all time)',`<span class="${cls(a.realized)}">${money(a.realized)}</span>`,'after charges')+
  card('Open P&L',`<span class="${cls(a.open)}">${money(a.open)}</span>`,'live, not yet booked')+
  card('Margin used','₹'+Math.round(a.margin).toLocaleString('en-IN'),'spread width × qty')+
  card('Available funds',`<span class="${a.available<0?'dn':''}">₹${Math.round(a.available).toLocaleString('en-IN')}</span>`,'equity − margin')+
  card('Peak equity','₹'+Math.round(a.peak).toLocaleString('en-IN'),'')+card('Max drawdown',`<span class="${a.maxdd>0?'dn':''}">₹${Math.round(a.maxdd).toLocaleString('en-IN')}</span>`,'from peak');
 $('acctnote').textContent=a.resets?`${a.resets} reset(s) so far`:'';}
function clearAll(){const a=(S&&S.account)||{start:500000};
 const v=prompt('FRESH START\n\nThis permanently deletes ALL trade history, realized P&L, logs, and any open or pending trades (they are removed, NOT squared off).\nYour settings are kept. Chart trigger lines are removed too, because they belong to the deleted trades.\n\nEnter the STARTING EQUITY for the new paper account in ₹ (e.g. 500000).\nPress Cancel to abort:',Math.round(a.start));
 if(v===null)return;const eq=parseFloat(String(v).replace(/[,₹\s]/g,''));
 if(!(eq>=1000)){alert('Please enter a number of at least 1000.');return;}
 const t=prompt(`Last check. Type CLEAR to delete everything and start fresh with ₹${eq.toLocaleString('en-IN')}:`);
 if(t===null||t.trim().toUpperCase()!=='CLEAR'){alert('Cancelled. Nothing was deleted.');return;}
 post('/api/account/reset',{equity:eq}).then(()=>{setTimeout(()=>{poll();loadTrades();},1500);});}

let snd=true,prevSpot=null;
function beep(){try{const a=new(window.AudioContext||window.webkitAudioContext)(),o=a.createOscillator(),g=a.createGain();o.connect(g);g.connect(a.destination);o.frequency.value=880;g.gain.value=.06;o.start();o.stop(a.currentTime+.18);}catch(e){}}
$('bsnd').onclick=()=>{snd=!snd;$('bsnd').textContent=snd?'🔔':'🔕';};
$('bLive').onclick=()=>chart.timeScale().scrollToRealTime();
$('bFit').onclick=()=>chart.timeScale().fitContent();
$('bFull').onclick=()=>document.body.classList.toggle('full');
$('bHelp').onclick=()=>{const h=$('help');h.style.display=h.style.display==='block'?'none':'block';};
const KEYS={v:'cursor',t:'trend',r:'ray',h:'h',c:'cline',a:'trig',f:'fib',m:'measure',b:'rect',x:'text'},TFK={1:1,2:3,3:5,4:15,5:30,6:60};
document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)||e.ctrlKey||e.metaKey)return;
 const k=e.key.toLowerCase();
 if(KEYS[k]){tool=KEYS[k];pending=null;buildTools();hint();}
 else if(TFK[k]){tf=TFK[k];[...$('tfSeg').children].forEach(b=>b.classList.toggle('act',b.textContent===({1:'1m',3:'3m',5:'5m',15:'15m',30:'30m',60:'1h'})[tf]));loadFull();}
 else if(e.key==='?')$('bHelp').click();});
function ribbon(){const s=S;if(!s)return;const sp=s.spot;
 $('spotbig').innerHTML=sp==null?'':`<span style="color:${prevSpot==null||sp>=prevSpot?'var(--up)':'var(--dn)'}">${fx(sp)}</span><small style="color:var(--mut);font-size:11px;margin-left:5px">NIFTY</small>`;if(sp!=null)prevSpot=sp;
 const p=s.pnl;$('pnlbig').innerHTML=p?`<span class="${cls(p.net)}">${money(p.net)}</span><small>${p.target_pct.toFixed(0)}% of target</small>`:'';}

/* ---------- backtest replay (tick by tick) ---------- */
let BT=false;const RP={T:[],P:[],i:0,play:false,speed:1,vt:0,lt:0,date:'',key:'',label:''};
function btBucket(t){const s=t+19800,st=Math.floor(s/86400)*86400+33300;return st+Math.floor((s-st)/(tf*60))*tf*60;}
function rpAdd(t,p){const b=btBucket(t),l=bars[bars.length-1];if(!l||b>l.time){bars.push({time:b,open:p,high:p,low:p,close:p});}else if(b===l.time){l.high=Math.max(l.high,p);l.low=Math.min(l.low,p);l.close=p;}}
function rpClock(){const t=RP.T[Math.max(0,RP.i-1)];$('rpClock').textContent=t?new Date((t+19800)*1000).toISOString().substr(11,8):'';$('rpSlider').value=RP.T.length?Math.round(RP.i/RP.T.length*1000):0;}
function rpRebuild(n){bars=[];for(let k=0;k<n;k++)rpAdd(RP.T[k],RP.P[k]);RP.i=n;cs.setData(bars);RP.vt=n?RP.T[n-1]:(RP.T[0]||0);rpClock();}
function rpReload(){rpRebuild(RP.i);const n=bars.length;if(n)chart.timeScale().setVisibleLogicalRange({from:Math.max(0,n-120),to:n+8});}
function rpFrame(ts){if(!RP.play)return;const dt=Math.min(0.25,(ts-RP.lt)/1000);RP.lt=ts;const n0=bars.length;
 if(RP.speed==='max'){const e=Math.min(RP.T.length,RP.i+6000);for(;RP.i<e;RP.i++)rpAdd(RP.T[RP.i],RP.P[RP.i]);}
 else{RP.vt+=dt*RP.speed;while(RP.i<RP.T.length&&RP.T[RP.i]<=RP.vt){rpAdd(RP.T[RP.i],RP.P[RP.i]);RP.i++;}}
 if(bars.length-n0>1)cs.setData(bars);else if(bars.length)cs.update(bars[bars.length-1]);
 rpClock();if(RP.i>=RP.T.length){RP.play=false;$('rpPlay').textContent='▶ Play';return;}requestAnimationFrame(rpFrame);}
function rpToggle(){if(!RP.T.length)return;if(RP.i>=RP.T.length)rpRebuild(1);RP.play=!RP.play;$('rpPlay').textContent=RP.play?'⏸ Pause':'▶ Play';if(RP.play){RP.lt=performance.now();requestAnimationFrame(rpFrame);}}
async function rpLoad(){RP.play=false;$('rpPlay').textContent='▶ Play';$('rpSrc').textContent='loading…';
 const d=await (await fetch(`/api/replay/data?date=${RP.date}&key=${encodeURIComponent(RP.key||'')}`)).json();
 RP.T=d.T;RP.P=d.P;RP.key=d.key;RP.label=d.label;label='Backtest '+RP.date+' '+d.label;
 $('rpKey').innerHTML=d.keys.map(k=>`<option value="${k.key}" ${k.key===d.key?'selected':''}>${k.label}</option>`).join('');
 $('rpSrc').textContent=d.source==='ticks'?`${RP.T.length.toLocaleString()} recorded ticks`:(RP.T.length?'⚠ no tick file for this day: rebuilt from 1-minute candles':'⚠ no data for this day');
 setLevels([],[]);loadDrawings();rpRebuild(Math.min(1,RP.T.length));rpReload();}
async function rpInit(){const ds=await (await fetch('/api/replay/dates')).json();
 $('rpDate').innerHTML=ds.map(x=>`<option value="${x.d}">${x.d} ${x.src==='ticks'?'(recorded ticks)':'(candles only)'}</option>`).join('');
 if(!RP.date||!ds.some(x=>x.d===RP.date))RP.date=ds.length?ds[0].d:'';if(RP.date){$('rpDate').value=RP.date;await rpLoad();}else $('rpSrc').textContent='no days available yet';}
function btSwitch(){$('rpbar').style.display=BT?'inline-flex':'none';document.body.classList.toggle('bt',BT);
 if(BT){rpInit();}else{RP.play=false;bars=[];cs.setData([]);lastLv='';lastMk='';loadFull();}}
$('rpPlay').onclick=rpToggle;$('rpRestart').onclick=()=>{RP.play=false;$('rpPlay').textContent='▶ Play';rpRebuild(Math.min(1,RP.T.length));rpReload();};
$('rpSpeed').onchange=e=>{RP.speed=e.target.value==='max'?'max':+e.target.value;};
$('rpDate').onchange=e=>{RP.date=e.target.value;RP.key='';rpLoad();};$('rpKey').onchange=e=>{RP.key=e.target.value;rpLoad();};
$('rpSlider').oninput=e=>{const n=Math.max(1,Math.round(e.target.value/1000*RP.T.length));rpRebuild(n);rpReload();};
async function poll(){try{S=await (await fetch('/api/state')).json();render();}catch(e){$('pfeed').textContent='server offline';$('pfeed').style.background='#d73027';}}
$('bsq').onclick=()=>{if(Date.now()<sqArm){sqArm=0;$('bsq').textContent='Square off ALL';post('/api/cmd',{cmd:'EXIT',pid:'ALL'});}else{sqArm=Date.now()+6000;$('bsq').textContent='CONFIRM square off (6s)';}};
$('benp').onclick=()=>post('/api/cmd',{cmd:'ENTER',side:'PUT'});
$('bexp').onchange=e=>post('/api/cmd',{cmd:'EXPIRY',exp:e.target.value});
let EXPL='';function expSel(s){const el=$('bexp');if(!el)return;el.style.display=s.mode==='futures'||!(s.exp_list||[]).length?'none':'';const k=(s.exp_list||[]).join(',');
 if(k!==EXPL){EXPL=k;el.innerHTML=(s.exp_list||[]).map(x=>{const d=new Date(x+'T00:00:00');return `<option value="${x}">Expiry ${d.toLocaleDateString('en-IN',{weekday:'short',day:'2-digit',month:'short'})}</option>`;}).join('');}
 if(document.activeElement!==el&&s.trade_exp)el.value=s.trade_exp;}
$('bbuy').onclick=()=>post('/api/cmd',{cmd:'ENTER',side:'BUY'});
$('bsell').onclick=()=>post('/api/cmd',{cmd:'ENTER',side:'SELL'});
$('benc').onclick=()=>post('/api/cmd',{cmd:'ENTER',side:'CALL'});
$('bauto').onclick=()=>post('/api/cmd',{cmd:S&&S.auto?'AUTO_OFF':'AUTO_ON'});
$('bstop').onclick=()=>{if(confirm('Stop the engine and exit? An open trade stays saved and resumes next run.'))post('/api/cmd',{cmd:'STOP'});};
$('applyset').onclick=()=>{const o={};document.querySelectorAll('#settings [data-k]').forEach(e=>{o[e.dataset.k]=e.type==='checkbox'?e.checked:e.value;});post('/api/settings',o);};

let view=localStorage.getItem('view')||'split';
const VIEWS=[['chart','📈 Chart'],['split','◫ Split'],['position','📋 Position'],['signals','📡 Signals'],['score','🏆 Scorecard'],['trades','📒 Trades'],['settings','⚙ Settings']];
function setView(v){const prev=view;view=v;setBuilt=false;document.body.dataset.view=v;try{localStorage.setItem('view',v);}catch(e){}
 [...$('nav').children].forEach(b=>b.classList.toggle('act',b.dataset.v===v));
 if(v==='trades')loadTrades();if(v==='settings')buildSettings();if(v==='score')loadScore();
 if(['position','signals','score','trades','settings'].includes(prev)&&['chart','split'].includes(v))setTimeout(loadFull,200);}
VIEWS.forEach(([v,t])=>{const b=document.createElement('button');b.textContent=t;b.dataset.v=v;b.onclick=()=>setView(v);$('nav').appendChild(b);});
async function loadTrades(){try{const d=await (await fetch('/api/trades')).json(),st=d.stats;
 $('tstats').innerHTML=card('Closed trades',st.n,`${st.wins} wins · ${st.win_rate.toFixed(0)}% win rate`)+card('Total net',`<span class="${cls(st.total)}">${money(st.total)}</span>`,'after charges')+
  card('Average / trade',money(st.avg),'')+card('Best / worst',`${money(st.best)} / ${money(st.worst)}`,'')+card('Total charges','₹'+Math.round(st.charges),'');
 const H=['Trade','Entry','Exit','Held','Strikes','Credit','Exit debit','Spot in→out','Δ in→out','Gross','Charges','Net','Best','Worst','Reason'];
 $('ttable').innerHTML=d.rows.length?'<table><tr>'+H.map(h=>`<th>${h}</th>`).join('')+'</tr>'+d.rows.map(t=>`<tr><td>${t.trade_id}</td><td>${String(t.entry_time).slice(5,16)}</td><td>${String(t.exit_time).slice(5,16)}</td>
  <td>${t.duration_min}m</td><td>${t.short_strike}/${t.long_strike}</td><td>${fx(t.entry_credit_pts)}</td><td>${fx(t.exit_debit_pts)}</td><td>${fx(t.entry_spot,0)}→${fx(t.exit_spot,0)}</td>
  <td>${fx(t.entry_short_delta,2)}→${fx(t.exit_short_delta,2)}</td><td>${money(t.gross_pnl)}</td><td>₹${Math.round(t.total_charges)}</td><td class="${cls(t.net_pnl)}"><b>${money(t.net_pnl)}</b></td>
  <td>${money(t.best_net_pnl)}</td><td>${money(t.worst_net_pnl)}</td><td>${t.exit_reason}</td></tr>`).join('')+'</table>':'<p style="opacity:.6">No closed trades yet.</p>';}catch(e){}}
setInterval(()=>{if(view==='trades')loadTrades();},5000);

let sigKey='';const SV=n=>{const x=((S&&S.settings)||[]).find(y=>y.name===n);return x?x.value:null;};
const setSet=(n,v)=>{post('/api/settings',{[n]:v});if(document.activeElement)document.activeElement.blur();sigKey='';};
const SCOL={BULL:'#22c55e',BEAR:'#ef4444',NEUTRAL:'#8696ab',NA:'#4b5a70'},SLBL={BULL:'BULLISH',BEAR:'BEARISH',NEUTRAL:'NEUTRAL',NA:'NO DATA'};
const tog=(n,t)=>`<label style="display:block;margin:4px 0"><input type="checkbox" ${SV(n)?'checked':''} onchange="setSet('${n}',this.checked)"> ${t}</label>`;
function srHtml(L){if(!L||!L.spot)return '<span style="color:var(--mut)">Collecting price history…</span>';
 const row=x=>{const d=x.price-L.spot;return `<tr style="${Math.abs(d)<=L.near?'background:#3a2a10':''}"><td><b>${fx(x.price,0)}</b></td><td>${d>=0?'+':''}${d.toFixed(0)} pts (${(d/L.spot*100).toFixed(2)}%)</td><td>turned back ${x.touches}×</td><td>${x.tfs.join(', ')}</td><td>${x.last}</td></tr>`;};
 const tbl=a=>a.length?`<table><tr><th>Level</th><th>Distance</th><th>History</th><th>Seen on</th><th>Last</th></tr>${a.map(row).join('')}</table>`:'<span style="color:var(--mut)">none found nearby</span>';
 const r=L.resist[0],s=L.support[0];
 const sm=`Nifty ${fx(L.spot)}. `+(r?`Nearest <b style="color:#e0a04a">resistance ${fx(r.price,0)}</b> (${(r.price-L.spot).toFixed(0)} pts up): price has turned back from here ${r.touches}× (${r.tfs.join(', ')}). `:'No resistance found above. ')+(s?`Nearest <b style="color:#4aa3ff">support ${fx(s.price,0)}</b> (${(L.spot-s.price).toFixed(0)} pts down): price has bounced from here ${s.touches}× (${s.tfs.join(', ')}).`:'No support found below.');
 return `<div class="card" style="margin-bottom:10px">${sm}</div><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px"><div class="card"><div class="l" style="color:#e0a04a">Resistance above: price tends to fall back here</div>${tbl(L.resist)}</div><div class="card"><div class="l" style="color:#4aa3ff">Support below: price tends to bounce here</div>${tbl(L.support)}</div></div><p style="color:var(--mut);font-size:12px">A level counts when price turned around there on the 5-minute, 15-minute or 1-hour chart, or at yesterday's high or low. Levels seen on longer charts are stronger. A highlighted row means price is right at it now.</p>`;}
let posSig='';
function selPos(pid){post('/api/select',{pid});if(S)S.sel=pid;setTimeout(()=>{loadFull();poll();},150);}
function partialPos(pid,lots){const v=prompt('This trade has '+lots+' lots. How many lots do you want to close now? (1 to '+(lots-1)+')',Math.max(1,Math.floor(lots/2)));if(v)post('/api/cmd',{cmd:'PARTIAL',pid,lots:+v});}
function delPos(pid){if(confirm('Delete trade #'+pid+' completely, as if it was never taken?\n\nNo exit order is simulated. It is removed from positions and trade history, and any profit/loss it already booked is taken back out of your account. This cannot be undone.'))post('/api/cmd',{cmd:'DELETE',pid});}
function exitPos(pid,st){if(confirm(st==='WAITING'?'Cancel this pending trade?':'Square off this trade now?'))post('/api/cmd',{cmd:'EXIT',pid});}
$('possel').onchange=e=>selPos(+e.target.value);
function renderPositions(){const P=(S&&S.positions)||[];if(!S)return;
 const sg=P.map(p=>p.pid+p.label+p.status).join('|');if(sg!==posSig){posSig=sg;$('possel').innerHTML=P.map(p=>`<option value="${p.pid}">#${p.pid} ${p.label} (${p.status.toLowerCase()})</option>`).join('');}
 if(document.activeElement!==$('possel'))$('possel').value=S.sel;
 $('poslist').innerHTML=P.length?`<table><tr><th></th><th>Trade</th><th>State</th><th>Net P&L</th><th>Qty</th><th>Since</th><th></th></tr>`+P.map(p=>`<tr style="cursor:pointer;${p.pid===S.sel?'background:#1b2a42':''}" onclick="selPos(${p.pid})"><td>${p.pid===S.sel?'●':'○'}</td><td>#${p.pid} ${p.label}</td><td>${p.status.toLowerCase()}</td><td class="${cls(p.net)}">${p.status==='WAITING'?'–':money(p.net)}</td><td>${p.qty}</td><td>${p.entry}</td><td>${p.status!=='CLOSED'?`${p.status==='OPEN'&&p.lots>=2?`<button class="xbtn" title="Close part of this trade" onclick="event.stopPropagation();partialPos(${p.pid},${p.lots})">Partial</button> `:''}<button class="xbtn" onclick="event.stopPropagation();exitPos(${p.pid},'${p.status}')">${p.status==='OPEN'?'Exit':'Cancel'}</button>`:`<span style="color:var(--mut)">${p.reason}</span>`} <button class="xbtn" title="Remove this trade completely, as if it was never taken" onclick="event.stopPropagation();delPos(${p.pid})">Delete</button></td></tr>`).join('')+'</table>':'';}
function renderSignals(){const g=S&&S.signals;if(!g||view!=='signals')return;
 const f=g.fut||{};$('sigFut').innerHTML=!f.key?'⚠ Futures contract not found, so the VWAP and volume signals have no data (see the terminal).':(f.age!=null&&f.age<30?'🟢':'🟠')+` Futures feed: ${f.key} · last price ${f.ltp==null?'–':fx(f.ltp)} · volume today ${Math.round(f.vol).toLocaleString('en-IN')} · ${f.age==null?'no ticks yet':'updated '+Math.round(f.age)+'s ago'}`;
 if(document.activeElement&&document.activeElement.closest&&document.activeElement.closest('#vSignals'))return;
 const k=JSON.stringify(g)+JSON.stringify((S.settings||[]).filter(x=>/^SIG_|INTRADAY/.test(x.name)).map(x=>x.value));if(k===sigKey)return;sigKey=k;
 const vc=SCOL[g.verdict],cm=SV('SIG_COMBINE');
 $('sigTop').innerHTML=`<div class="card" style="border-color:${vc}"><div class="l">Overall view</div><div class="v" style="color:${vc}">${SLBL[g.verdict]}</div><div class="s">${g.detail}</div>
  <div class="s" style="margin-top:6px">${g.verdict==='BULL'?'→ plan: sell a bull put spread':g.verdict==='BEAR'?'→ plan: sell a bear call spread':'→ no trade'} · updated ${g.updated}</div></div>
  <div class="card"><div class="l">When should the system act?</div><select onchange="setSet('SIG_COMBINE',this.value)" style="margin:6px 0">${[['all','Every ticked signal agrees'],['any','At least one agrees and none disagree'],['majority','Most of the ticked signals agree']].map(o=>`<option value="${o[0]}" ${cm===o[0]?'selected':''}>${o[1]}</option>`).join('')}</select>
  ${tog('SIG_AUTO_TRADE','Trade automatically (practice money): bullish → bull put spread, bearish → bear call spread')}${tog('SIG_EXIT_ON_OPPOSITE','Close my trade if the signals turn against it')}${tog('SIG_NOTIFY','Alert me whenever a signal changes')}${tog('INTRADAY_ONLY','Same-day trading only (close everything at the end-of-day time)')}</div>
  <div class="card" style="grid-column:1/-1"><div class="l">Fine tuning</div>${['SIG_VWAP_BUFFER','DMA_FAST','DMA_SLOW','DMA_LONG','SIG_RVOL_MIN','SIG_SLOPE_MIN','SIG_OI_BUFFER','SIG_CONTRIB_MIN','SIG_COOLDOWN_MIN','SIG_MAX_PER_SIDE','SR_TOL_PCT'].map(k=>`<div class="srow"><div>${LB[k][0]}<div class="h">${LB[k][1]}</div></div><div><input data-k="${k}" value="${SV(k)}"></div></div>`).join('')}<button class="green" onclick="saveFine()" style="margin-top:8px">Save</button></div>`;
 $('sigCards').innerHTML=g.items.map(i=>`<div class="card" style="border-left:4px solid ${SCOL[i.state]}"><div style="display:flex;justify-content:space-between;align-items:center"><b>${i.name}</b><span class="badge" style="background:${SCOL[i.state]}">${SLBL[i.state]}</span></div>
  <div class="s" style="margin:6px 0">${i.rule}</div><div style="margin:4px 0">${i.detail}</div><div class="s" style="color:${SCOL[i.state]}">${i.predict}</div>
  <label style="display:block;margin-top:8px"><input type="checkbox" ${i.enabled?'checked':''} onchange="setSet('${i.use}',this.checked)"> use this signal</label></div>`).join('');
 $('sigSR').innerHTML=srHtml(g.levels);
 $('sigLog').innerHTML=g.log.length?'<table><tr><th>Time</th><th>Signal</th><th>Now</th><th>Detail</th></tr>'+g.log.map(l=>`<tr><td>${l.t}</td><td>${l.name}</td><td style="color:${SCOL[l.state]}"><b>${SLBL[l.state]}</b></td><td style="text-align:left">${l.detail}</td></tr>`).join('')+'</table>':'<span style="color:var(--mut)">No signal changes yet today.</span>';}
try{if(window.Notification&&Notification.permission==='default')Notification.requestPermission();}catch(e){}
setView(view);
loadFull();poll();setInterval(poll,1000);setInterval(tailPoll,1000);
</script></body></html>"""

if __name__ == "__main__":
    main()
