"""
CHAIN RECORDER - saves the whole option chain once a minute, plus India VIX, plus live ticks of the strikes near the money.

  * Every minute (09:14-15:31 IST): all strikes within +-1500 pts of Nifty for the nearest expiry and +-500 pts for the next
    expiry: bid/ask + sizes, last price, open interest (and yesterday's), volume, implied volatility, delta/theta/gamma/vega,
    for calls and puts. File: spread_data/ticks/ticks_chain-<day>.csv
  * The next two MONTHLY expiries (when they are not already one of the two nearest): strikes from 2600 below to 1200 above Nifty,
    every minute around the open (09:14-09:46) and the close (15:00-15:31), every 5 minutes in between. Same file and columns.
  * India VIX every ~20 s.                       File: spread_data/ticks/ticks_vix-<day>.csv
  * A small daily facts file (expiries, lot size, which keys are which).  File: spread_data/ticks/ticks_meta-<day>.csv
  * feed_keys(): the nearest TICK_STRIKES strikes each side of the money (calls and puts) are added to the live feed, so their
    individual ticks are saved like the others (ticks_<day>.csv).
These files start with "ticks_" so the existing cloud saver (persist.py) sends them to the nifty-ticks repo as small pieces.
Read-only: it never trades and never raises into the main app.
"""
import os, csv, time, threading
from datetime import datetime, timedelta, timezone
try:
    import requests
except Exception:
    requests = None

IST = timezone(timedelta(hours=5, minutes=30))
TICK_STRIKES = 10          # strikes each side of the money whose ticks are recorded
NEAR_RANGE, NEXT_RANGE = 1500, 500
MONTHLY_DOWN, MONTHLY_UP = 2600, 1200     # the next 2 MONTHLY expiries too (Option C, L1 and Q trade them; Q sells puts ~6-9% below Nifty)
MONTHLY_EVERY_S = 300                     # every 5 minutes, but every minute 09:14-09:46 and 15:00-15:31 (entries and exits happen then)
EVERY_S, VIX_EVERY_S = 60, 20
VIX_KEY = "NSE_INDEX|India VIX"

G = {}
KEYS = []                  # option keys currently asked for in the live feed
_ATM = [None]
STATUS = dict(snaps=0, rows=0, vix=0, last_ok=None, last_err=None)
CH_COLS = ["ts", "expiry", "dte", "strike", "spot", "pcr"]
for _s in ("c", "p"):
    CH_COLS += [f"{_s}_key", f"{_s}_ltp", f"{_s}_bid", f"{_s}_bidq", f"{_s}_ask", f"{_s}_askq", f"{_s}_oi", f"{_s}_prev_oi", f"{_s}_vol", f"{_s}_close",
                f"{_s}_iv", f"{_s}_delta", f"{_s}_theta", f"{_s}_gamma", f"{_s}_vega"]

def g(n, d=None): return G.get(n, d)

def _f(x):
    try:
        v = float(x); return v if v == v else None
    except Exception: return None

def _dir(): return g("TICKS_DIR")

def _append(name, cols, rows, header_if_new=True):
    path = os.path.join(_dir(), name); new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new and header_if_new: w.writerow(cols)
        w.writerows(rows)

def _err(where, e):
    STATUS["last_err"] = f"{datetime.now(IST):%H:%M:%S} {where}: {e!r}"[:300]
    print("[chainrec]", STATUS["last_err"], flush=True)

def feed_keys():
    return list(KEYS)

def _get(url, params):
    r = requests.get(url, headers=g("HDR"), params=params, timeout=20); r.raise_for_status(); return r.json()

_LAST_MONTHLY = [0.0]
def _monthlies(all_exps, skip):
    last = {}
    for e in all_exps: last[(e.year, e.month)] = max(last.get((e.year, e.month), e), e)
    return [e for e in sorted(last.values()) if e not in skip][:2]

def _snapshot(nw):
    today = nw.date(); day = f"{nw:%Y-%m-%d}"
    all_exps = [e for e in g("exp_choices")() if e >= today]; exps = all_exps[:2]
    ts = round(time.time(), 1); out = []; first_rows = None
    mins = nw.hour * 60 + nw.minute; busy = 9 * 60 + 14 <= mins <= 9 * 60 + 46 or mins >= 15 * 60
    mon = _monthlies(all_exps, exps) if (busy or time.time() - _LAST_MONTHLY[0] >= MONTHLY_EVERY_S - 5) else []
    if mon: _LAST_MONTHLY[0] = time.time()
    for i, e in enumerate(exps + mon):
        data = _get("https://api.upstox.com/v2/option/chain", {"instrument_key": g("UNDERLYING_KEY"), "expiry_date": str(e)}).get("data") or []
        spot = next((_f(x.get("underlying_spot_price")) for x in data if _f(x.get("underlying_spot_price"))), None)
        if not spot: continue
        rng = NEAR_RANGE if i == 0 else NEXT_RANGE
        for x in data:
            k = _f(x.get("strike_price"))
            if k is None: continue
            if i >= len(exps):
                if not (-MONTHLY_DOWN <= k - spot <= MONTHLY_UP): continue
            elif abs(k - spot) > rng: continue
            row = [ts, str(e), (e - today).days, k, spot, x.get("pcr")]
            for side in ("call_options", "put_options"):
                o = x.get(side) or {}; m = o.get("market_data") or {}; gr = o.get("option_greeks") or {}
                row += [o.get("instrument_key"), m.get("ltp"), m.get("bid_price"), m.get("bid_qty"), m.get("ask_price"), m.get("ask_qty"), m.get("oi"), m.get("prev_oi"),
                        m.get("volume"), m.get("close_price"), gr.get("iv"), gr.get("delta"), gr.get("theta"), gr.get("gamma"), gr.get("vega")]
            out.append(row)
        if i == 0: first_rows = (data, spot, e)
    if out: _append(f"ticks_chain-{day}.csv", CH_COLS, out); STATUS["snaps"] += 1; STATUS["rows"] += len(out)
    if first_rows: _update_keys(*first_rows)
    return exps

def _update_keys(data, spot, e):
    atm = round(spot / 50.0) * 50
    if _ATM[0] is not None and abs(atm - _ATM[0]) < 100 and KEYS: return          # keep the set steady until the market moves 100 pts
    _ATM[0] = atm; ks = []
    want = {atm + 50 * j for j in range(-TICK_STRIKES, TICK_STRIKES + 1)}
    for x in data:
        if _f(x.get("strike_price")) in want:
            for side in ("call_options", "put_options"):
                k = (x.get(side) or {}).get("instrument_key")
                if k: ks.append(k)
    KEYS[:] = list(dict.fromkeys(ks))

def _meta(nw, exps):
    day = f"{nw:%Y-%m-%d}"; path = os.path.join(_dir(), f"ticks_meta-{day}.csv")
    if os.path.exists(path): return
    rows = [[round(time.time(), 1), "lot_size", g("LOT")], [round(time.time(), 1), "expiries", "|".join(str(e) for e in exps)],
            [round(time.time(), 1), "futures_key", g("FUT_KEY")], [round(time.time(), 1), "underlying_key", g("UNDERLYING_KEY")],
            [round(time.time(), 1), "rate_assumed", g("R_")], [round(time.time(), 1), "div_yield_assumed", g("Q_")]]
    _append(f"ticks_meta-{day}.csv", ["ts", "item", "value"], rows)

def _vix(nw):
    d = _get("https://api.upstox.com/v2/market-quote/quotes", {"instrument_key": VIX_KEY}).get("data") or {}
    q = next(iter(d.values()), {}) if d else {}
    v = _f(q.get("last_price"))
    if v is None: return
    _append(f"ticks_vix-{nw:%Y-%m-%d}.csv", ["ts", "vix", "prev_close"], [[round(time.time(), 1), v, (q.get("ohlc") or {}).get("close")]]); STATUS["vix"] += 1

def _loop():
    last_snap = last_vix = 0.0; exps = []
    while True:
        time.sleep(2)
        try:
            nw = datetime.now(IST); mins = nw.hour * 60 + nw.minute
            if nw.weekday() >= 5 or not (9 * 60 + 14 <= mins <= 15 * 60 + 31) or not g("HDR"): continue
            t = time.time()
            if t - last_snap >= EVERY_S:
                last_snap = t
                try: exps = _snapshot(nw); _meta(nw, exps); STATUS["last_ok"] = f"{nw:%H:%M:%S}"
                except Exception as e: _err("snapshot", e)
            if t - last_vix >= VIX_EVERY_S:
                last_vix = t
                try: _vix(nw)
                except Exception as e: _err("vix", e)
        except Exception as e: _err("loop", e)

def status_line():
    return f"chain snaps {STATUS['snaps']} rows {STATUS['rows']} | vix {STATUS['vix']} | live option keys {len(KEYS)} | last ok {STATUS['last_ok'] or 'not yet'}" + (f" | err {STATUS['last_err']}" if STATUS["last_err"] else "")

def start(globals_dict):
    global G
    G = globals_dict
    if requests is None: return
    threading.Thread(target=_loop, daemon=True, name="chainrec").start()
