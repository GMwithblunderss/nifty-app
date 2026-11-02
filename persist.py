"""
Cloud persistence for the Nifty dashboard (Render's free plan has no permanent disk).

Active only when DATA_REPO (owner/name) and GH_TOKEN are set; on your own PC it does nothing.
  1. At start-up it copies saved files back from a private GitHub data repo, so a restart loses nothing.
  2. Every 10 minutes it copies new/changed files into that repo and pushes them.
  3. On shutdown (SIGTERM) and just before the daily 08:30 IST restart it pushes one last time.
NOT synced: spread_data/ticks (tens of MB a day) and the daily instruments download.
"""
import os, sys, time, shutil, subprocess, threading, signal, gzip
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "spread_data")
SR_DIR = os.path.join(BASE, "signal_research")
MAP = [("spread_data", DATA_DIR), ("signal_research", SR_DIR)]
IST = timezone(timedelta(hours=5, minutes=30))
REPO = os.environ.get("DATA_REPO", "").strip()
TOKEN = os.environ.get("GH_TOKEN", "").strip()
URL = os.environ.get("DATA_REPO_URL", "").strip() or (f"https://x-access-token:{TOKEN}@github.com/{REPO}.git" if REPO and TOKEN else "")
CLONE = os.environ.get("DATA_CLONE_DIR", "/tmp/nifty_data_repo")
PUSH_EVERY = int(os.environ.get("PUSH_EVERY_SEC", "600"))
RESTART_AT = os.environ.get("DAILY_RESTART_IST", "08:30")
MAX_FILE = 95 * 1024 * 1024            # GitHub rejects files over 100 MB
ENABLED = bool(URL)
# raw ticks go to a SEPARATE repo as small compressed pieces (a growing 70 MB file cannot be pushed to GitHub)
TICKS_REPO = os.environ.get("TICKS_REPO", "").strip()
TURL = os.environ.get("TICKS_REPO_URL", "").strip() or (f"https://x-access-token:{TOKEN}@github.com/{TICKS_REPO}.git" if TICKS_REPO and TOKEN else "")
TCLONE = os.environ.get("TICKS_CLONE_DIR", "/tmp/nifty_ticks_repo")
# preferred home for raw ticks: a private Hugging Face dataset (100 GB free). When HF_REPO + HF_TOKEN are set, ticks go there
# instead of the GitHub ticks repo (same piece files, same folder layout: ticks/<day>/ticks_<day>_<stamp>.csv.gz).
HF_REPO = os.environ.get("HF_REPO", "").strip()          # e.g. yourname/nifty-ticks
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
HF_ON = bool(HF_REPO and HF_TOKEN)
TICKS_ON = HF_ON or bool(TURL)
TICKS_DIR = os.path.join(DATA_DIR, "ticks")
SELF_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")   # Render fills this in; the app pings itself so Render never puts it to sleep
PING_EVERY = 240
START_TS = time.time()
STATUS = {"last_ok": None, "last_err": None, "pushes": 0, "restored": 0, "skipped_big": [], "tick_chunks": 0, "ticks_ok": None, "ping": None}
_LOCK = threading.Lock()
_TLOCK = threading.Lock()
_TOFF = {}      # tick file name -> bytes already saved
_TSTAMP = [0]   # last piece stamp used; always increases, so two pieces can never share a file name
_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")


def _clean(s):
    s = s or ""
    if TOKEN: s = s.replace(TOKEN, "***")
    if HF_TOKEN: s = s.replace(HF_TOKEN, "***")
    return s


def _now():
    return datetime.now(IST).strftime("%d-%b %H:%M:%S")


def _err(msg):
    msg = _clean(msg)
    STATUS["last_err"] = f"{_now()} {msg}"
    print(f"[persist] {msg}", flush=True)


def _git(*args, timeout=180):
    r = subprocess.run(["git", "-C", CLONE, *args], capture_output=True, text=True, timeout=timeout, env=_ENV)
    return r.returncode, _clean((r.stdout or "") + (r.stderr or ""))


def _skip(rel):
    parts = rel.replace("\\", "/").split("/")
    name = parts[-1]
    return ("__pycache__" in parts[:-1] or name.startswith("instruments_")
            or (name.startswith("ticks_") and name.endswith(".csv"))        # big raw ticks go through push_ticks() instead
            or ".tmp" in name or name.endswith((".lock", ".py", ".pyc")))


def _ensure_clone():
    if os.path.isdir(os.path.join(CLONE, ".git")):
        return True
    os.makedirs(os.path.dirname(CLONE) or ".", exist_ok=True)
    r = subprocess.run(["git", "clone", "--depth", "1", "-q", URL, CLONE], capture_output=True, text=True, timeout=300, env=_ENV)
    if r.returncode != 0:
        _err("clone failed: " + (r.stderr or "").strip()[-300:])
        return False
    _git("config", "user.email", "nifty-bot@users.noreply.github.com")
    _git("config", "user.name", "nifty-bot")
    return True


def restore_early():
    """Copy saved files back before the app reads anything. Never overwrites a file that already exists."""
    if not ENABLED:
        return
    try:
        if not _ensure_clone():
            return
        n = 0
        for sub, dest in MAP:
            src = os.path.join(CLONE, sub)
            for root, _dirs, files in os.walk(src):
                for f in files:
                    p = os.path.join(root, f)
                    rel = os.path.relpath(p, src)
                    if _skip(rel):
                        continue
                    t = os.path.join(dest, rel)
                    if os.path.exists(t):
                        continue
                    os.makedirs(os.path.dirname(t), exist_ok=True)
                    shutil.copy2(p, t)
                    n += 1
        STATUS["restored"] = n
        print(f"[persist] restored {n} file(s) from the data repo", flush=True)
    except Exception as e:
        _err(f"restore failed: {e!r}")


def push_now(reason="auto"):
    if not ENABLED:
        return False
    with _LOCK:
        try:
            if not _ensure_clone():
                return False
            for sub, src in MAP:
                if not os.path.isdir(src):
                    continue
                for root, dirs, files in os.walk(src):
                    dirs[:] = [d for d in dirs if d != "__pycache__"]
                    for f in files:
                        p = os.path.join(root, f)
                        rel = os.path.relpath(p, src)
                        if _skip(rel):
                            continue
                        try:
                            st = os.stat(p)
                        except OSError:
                            continue
                        if st.st_size > MAX_FILE:
                            if rel not in STATUS["skipped_big"]:
                                STATUS["skipped_big"].append(rel)
                                _err(f"{rel} is over 95 MB, not synced")
                            continue
                        t = os.path.join(CLONE, sub, rel)
                        if os.path.exists(t):
                            ts = os.stat(t)
                            if ts.st_size == st.st_size and int(ts.st_mtime) >= int(st.st_mtime):
                                continue
                        os.makedirs(os.path.dirname(t), exist_ok=True)
                        shutil.copy2(p, t)
            _git("add", "-A")
            rc, out = _git("status", "--porcelain")
            if not out.strip():
                STATUS["last_ok"] = _now()
                return True
            rc, out = _git("commit", "-q", "-m", f"snapshot {datetime.now(IST):%Y-%m-%d %H:%M} IST ({reason})")
            if rc != 0:
                raise RuntimeError(out.strip()[-300:])
            rc, out = _git("push", "-q", "origin", "HEAD:refs/heads/main", timeout=300)
            if rc != 0:
                _git("pull", "--rebase", "-q", "origin", "main", timeout=300)
                rc, out = _git("push", "-q", "origin", "HEAD:refs/heads/main", timeout=300)
            if rc != 0:
                raise RuntimeError(out.strip()[-300:])
            STATUS["pushes"] += 1
            STATUS["last_ok"] = _now()
            STATUS["last_err"] = None
            return True
        except Exception as e:
            _err(f"push failed ({reason}): {e!r}")
            return False


def _tgit(*args, timeout=180):
    r = subprocess.run(["git", "-C", TCLONE, *args], capture_output=True, text=True, timeout=timeout, env=_ENV)
    return r.returncode, _clean((r.stdout or "") + (r.stderr or ""))


def _ensure_tclone():
    if os.path.isdir(os.path.join(TCLONE, ".git")):
        return True
    os.makedirs(os.path.dirname(TCLONE) or ".", exist_ok=True)
    # blobless + no checkout: downloads no file contents, so a restart never re-downloads weeks of ticks
    r = subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout", "-q", TURL, TCLONE],
                       capture_output=True, text=True, timeout=300, env=_ENV)
    if r.returncode != 0:
        _err("ticks clone failed: " + (r.stderr or "").strip()[-300:])
        return False
    _tgit("config", "user.email", "nifty-bot@users.noreply.github.com")
    _tgit("config", "user.name", "nifty-bot")
    if _tgit("rev-parse", "--verify", "-q", "HEAD")[0] == 0:
        _tgit("reset", "-q")                 # fill the index from the last commit so new files are ADDED to it
    return True


def push_ticks(reason="auto"):
    """Send only the ticks written since last time, as one small .csv.gz piece per day-file."""
    if not TICKS_ON or not os.path.isdir(TICKS_DIR):
        return False
    if HF_ON:
        return _push_ticks_hf(reason)
    with _TLOCK:
        try:
            if not _ensure_tclone():
                return False
            stamp, pending, newfiles = max(int(time.time() * 1000), _TSTAMP[0] + 1), {}, []
            _TSTAMP[0] = stamp
            for name in sorted(os.listdir(TICKS_DIR)):
                if not (name.startswith("ticks_") and name.endswith(".csv")):
                    continue
                path = os.path.join(TICKS_DIR, name)
                size, off = os.path.getsize(path), _TOFF.get(name, 0)
                if size < off:
                    off = 0                   # file was replaced: start again
                if size == off:
                    continue
                with open(path, "rb") as fh:
                    fh.seek(off)
                    data = fh.read(size - off)
                cut = data.rfind(b"\n")
                if cut < 0:
                    continue                  # only half a line so far: wait
                day = name[len("ticks_"):-len(".csv")]
                out = os.path.join(TCLONE, "ticks", day)
                os.makedirs(out, exist_ok=True)
                fname = f"ticks_{day}_{stamp}.csv.gz"
                with gzip.open(os.path.join(out, fname), "wb", compresslevel=9) as g:
                    g.write(data[:cut + 1])
                newfiles.append(f"ticks/{day}/{fname}")
                pending[name] = off + cut + 1
            if not pending:
                STATUS["ticks_ok"] = _now()
                return True
            # add ONLY the new pieces by name: this clone has no files checked out, so "add -A" would delete every earlier piece
            rc, out_ = _tgit("add", "--", *newfiles)
            if rc != 0:
                raise RuntimeError(out_.strip()[-300:])
            rc, out_ = _tgit("commit", "-q", "-m", f"ticks {datetime.now(IST):%Y-%m-%d %H:%M} IST ({reason})")
            if rc != 0:
                raise RuntimeError(out_.strip()[-300:])
            _TOFF.update(pending)             # committed locally: never cut these bytes again, even if the push has to be retried
            STATUS["tick_chunks"] += len(pending)
            rc, out_ = _tgit("push", "-q", "origin", "HEAD:refs/heads/main", timeout=300)
            if rc != 0:
                _tgit("pull", "--rebase", "-q", "origin", "main", timeout=300)
                rc, out_ = _tgit("push", "-q", "origin", "HEAD:refs/heads/main", timeout=300)
            if rc != 0:
                raise RuntimeError(out_.strip()[-300:])
            STATUS["ticks_ok"] = _now()
            return True
        except Exception as e:
            _err(f"ticks push failed ({reason}): {e!r}")
            return False


def _push_ticks_hf(reason="auto"):
    """Same pieces as the GitHub version, uploaded to the Hugging Face dataset in ONE commit (well under their hourly commit limit)."""
    with _TLOCK:
        try:
            from huggingface_hub import HfApi, CommitOperationAdd
            stamp, pending, ops, n = max(int(time.time() * 1000), _TSTAMP[0] + 1), {}, [], 0
            _TSTAMP[0] = stamp
            for name in sorted(os.listdir(TICKS_DIR)):
                if not (name.startswith("ticks_") and name.endswith(".csv")):
                    continue
                path = os.path.join(TICKS_DIR, name)
                size, off = os.path.getsize(path), _TOFF.get(name, 0)
                if size < off:
                    off = 0
                if size == off:
                    continue
                with open(path, "rb") as fh:
                    fh.seek(off)
                    data = fh.read(size - off)
                cut = data.rfind(b"\n")
                if cut < 0:
                    continue
                day = name[len("ticks_"):-len(".csv")]
                ops.append(CommitOperationAdd(path_in_repo=f"ticks/{day}/ticks_{day}_{stamp}.csv.gz",
                                              path_or_fileobj=gzip.compress(data[:cut + 1], compresslevel=9)))
                pending[name] = off + cut + 1
            if not ops:
                STATUS["ticks_ok"] = _now()
                return True
            HfApi(token=HF_TOKEN).create_commit(repo_id=HF_REPO, repo_type="dataset", operations=ops,
                                                commit_message=f"ticks {datetime.now(IST):%Y-%m-%d %H:%M} IST ({reason})")
            _TOFF.update(pending)             # only after the upload succeeded: a failed upload is simply retried next time
            STATUS["tick_chunks"] += len(ops)
            STATUS["ticks_ok"] = _now()
            return True
        except Exception as e:
            _err(f"ticks upload to Hugging Face failed ({reason}): {e!r}")
            return False


def _self_ping():
    try:
        import requests
        r = requests.get(SELF_URL + "/health", timeout=20)
        STATUS["ping"] = f"{_now()} {r.status_code}"
    except Exception as e:
        STATUS["ping"] = f"{_now()} FAILED {type(e).__name__}"


def status_line():
    parts = []
    parts.append(f"data sync on, last ok {STATUS['last_ok'] or 'not yet'}, pushes {STATUS['pushes']}, restored {STATUS['restored']}" if ENABLED else "data sync off")
    parts.append(f"ticks saved{' to Hugging Face' if HF_ON else ''}: {STATUS['tick_chunks']} pieces, last ok {STATUS['ticks_ok'] or 'not yet'}" if TICKS_ON else "ticks sync off")
    parts.append(f"self-ping {STATUS['ping'] or 'not yet'}" if SELF_URL else "self-ping off")
    if STATUS["skipped_big"]:
        parts.append("TOO BIG TO SYNC: " + ", ".join(STATUS["skipped_big"]))
    if STATUS["last_err"]:
        parts.append(f"LAST ERROR: {STATUS['last_err']}")
    return " | ".join(parts)


def _restart_due():
    if os.environ.get("DAILY_RESTART", "1") == "0":
        return False
    h, m = (int(x) for x in RESTART_AT.split(":"))
    now = datetime.now(IST)
    due = now.replace(hour=h, minute=m, second=0, microsecond=0)
    started = datetime.fromtimestamp(START_TS, IST)
    return due <= now < due + timedelta(minutes=10) and started < due and time.time() - START_TS > 3600


def start_background(on_stop=None):
    def stop_and_exit(reason):
        print(f"[persist] {reason}: saving before exit", flush=True)
        try:
            if on_stop:
                on_stop()
        except Exception:
            pass
        time.sleep(2)
        push_now(reason)
        push_ticks(reason)
        try: sys.stdout.flush(); sys.stderr.flush()
        except Exception: pass
        os._exit(0)

    try:
        signal.signal(signal.SIGTERM, lambda *_: stop_and_exit("SIGTERM"))
    except Exception:
        pass

    def loop():
        last = time.time()
        last_ping = 0.0
        while True:
            time.sleep(20)
            try:
                if _restart_due():
                    stop_and_exit("daily-restart")
                if SELF_URL and time.time() - last_ping >= PING_EVERY:
                    last_ping = time.time()
                    _self_ping()
                if time.time() - last >= PUSH_EVERY:
                    last = time.time()
                    if ENABLED:
                        push_now("auto")
                    if TICKS_ON:
                        push_ticks("auto")
            except Exception as e:
                _err(f"background loop: {e!r}")

    threading.Thread(target=loop, daemon=True, name="persist").start()
