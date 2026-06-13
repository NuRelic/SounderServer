#!/usr/bin/env python3
"""
Sound Server — LOCAL STAGING MOCK
=================================
A lean prototype to feel the hybrid design before folding it into soundboard_pi.py:

  - Old-server concurrency: format channels. 1 .wav + 1 .mp3 at a time by default,
    a new same-format fire INTERRUPTS the current one. Caps are adjustable up.
  - Unified feed: chat + fired commands + log in one stream. Buttons emit a !name
    line (and fire directly — chat never gates audio). Typed !name with live lookup.
  - Tabs (this pass): All (alphabetical) + Favorites + Add/Edit. Decentralised:
    audio plays in each browser; the server is the source of truth for what's active.

Local testing only. Not the production Pi code.
"""

import os
import io
import json
import time
import threading
import subprocess
import shutil
from collections import deque

from datetime import timedelta
import secrets as _secrets

from flask import (
    Flask, request, jsonify, send_file, render_template, abort, session
)
from werkzeug.utils import secure_filename

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
SOUND_DIR = os.environ.get("SOUND_DIR", os.path.expanduser("~/Downloads/Sounds"))
PORT      = int(os.environ.get("PORT", "5050"))

AUDIO_EXTS = {".wav", ".mp3"}          # the two format channels
LONG_THRESHOLD = 15.0                   # >15s = a "song" → the dedicated long lane
SYNC_BUFFER = 1.0                       # must match the frontend sync buffer
FAVS_FILE  = os.path.join(DATA_DIR, "favorites.json")
LIMITS_FILE= os.path.join(DATA_DIR, "limits.json")
DUR_FILE   = os.path.join(DATA_DIR, "durations.json")
BOXVOL_FILE= os.path.join(DATA_DIR, "box_volume.json")
FEED_FILE  = os.path.join(DATA_DIR, "feed_store.json")
SYNC_FILE  = os.path.join(DATA_DIR, "sync.json")
_FEED_TTL  = 3 * 86400                              # keep feed 3 days
YTDLP      = shutil.which("yt-dlp")

os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)

# ----------------------------------------------------------------------------
# Sessions / auth (staging)
#   edit rights = admin password OR an email login (stands in for the Pi's
#   "approved account" model). Both set can_edit; gated endpoints check it.
# ----------------------------------------------------------------------------
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin")          # admin (play + edit)
USER_PASS  = os.environ.get("USER_PASS", "")                # shared play-access password (set via env)
REQUIRE_LOGIN = os.environ.get("REQUIRE_LOGIN", "0") == "1" # public deploy → gate access behind the wall
_SECRET_FILE = os.path.join(DATA_DIR, ".flask_secret")
if os.path.exists(_SECRET_FILE):
    app.secret_key = open(_SECRET_FILE, "rb").read()
else:
    app.secret_key = _secrets.token_bytes(32)
    with open(_SECRET_FILE, "wb") as _f:
        _f.write(app.secret_key)
app.permanent_session_lifetime = timedelta(days=30)

def can_edit():
    return bool(session.get("admin") or session.get("can_edit"))

def has_access():
    return (not REQUIRE_LOGIN) or bool(session.get("play") or session.get("admin") or session.get("can_edit"))

def me_dict():
    return {"admin": bool(session.get("admin")),
            "email": session.get("email"),
            "can_edit": can_edit(),
            "play": has_access(),
            "needs_login": REQUIRE_LOGIN and not has_access()}

@app.before_request
def _gate_access():
    if not REQUIRE_LOGIN or has_access():
        return
    p = request.path
    if p == "/" or p.startswith("/static") or p in ("/api/login", "/api/logout", "/api/me"):
        return
    return jsonify({"ok": False, "error": "login required"}), 401

# ----------------------------------------------------------------------------
# Small JSON helpers
# ----------------------------------------------------------------------------
def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)

# ----------------------------------------------------------------------------
# Library index
# ----------------------------------------------------------------------------
# Keyed by the real filename (stable). cmd = filename without extension.
_LIB_LOCK = threading.Lock()
_LIBRARY = {}   # filename -> {"file","name","cmd","fmt"}

def scan_library():
    lib = {}
    try:
        for fn in os.listdir(SOUND_DIR):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in AUDIO_EXTS:
                continue
            full = os.path.join(SOUND_DIR, fn)
            if not os.path.isfile(full):
                continue
            stem = os.path.splitext(fn)[0]
            try: ver = int(os.path.getmtime(full))
            except OSError: ver = 0
            lib[fn] = {
                "file": fn,
                "name": stem,            # display
                "cmd": stem.lower(),     # !cmd trigger (lowercased)
                "fmt": ext.lstrip("."),  # wav | mp3
                "ver": ver,              # mtime — cache-buster so edits take effect
            }
    except FileNotFoundError:
        pass
    with _LIB_LOCK:
        _LIBRARY.clear()
        _LIBRARY.update(lib)
    return lib

# ----------------------------------------------------------------------------
# Durations (lazy probe + cache) — lets the channel auto-clear when a clip ends
# ----------------------------------------------------------------------------
_DUR = _load(DUR_FILE, {})
_DUR_LOCK = threading.Lock()

def duration(fn):
    with _DUR_LOCK:
        if fn in _DUR:
            return _DUR[fn]
    full = os.path.join(SOUND_DIR, fn)
    d = 5.0
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", full],
            capture_output=True, text=True, timeout=10,
        )
        d = float(out.stdout.strip())
    except Exception:
        pass
    with _DUR_LOCK:
        _DUR[fn] = d
        _save(DUR_FILE, _DUR)
    return d

_DUR_SCANNING = True   # background full-library duration probe in progress

def _ffprobe_dur(fn):
    full = os.path.join(SOUND_DIR, fn)
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", full],
            capture_output=True, text=True, timeout=10)
        return float(out.stdout.strip())
    except Exception:
        return None

def probe_all_durations():
    """One-time background probe so the UI knows which sounds are 'songs' (>15s)."""
    global _DUR_SCANNING
    try:
        with _LIB_LOCK:
            files = list(_LIBRARY.keys())
        todo = [f for f in files if f not in _DUR]
        if todo:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as ex:
                results = list(ex.map(lambda f: (f, _ffprobe_dur(f)), todo))
            with _DUR_LOCK:
                for f, d in results:
                    if d is not None:
                        _DUR[f] = d
                _save(DUR_FILE, _DUR)
    finally:
        _DUR_SCANNING = False

# ----------------------------------------------------------------------------
# Catalog + usage stats (SQLite) — seeded from the original sound server DB,
# kept live going forward (play counts increment on every fire).
# ----------------------------------------------------------------------------
import sqlite3
CATALOG_DB   = os.path.join(DATA_DIR, "catalog.db")
CATALOG_SEED = os.environ.get("CATALOG_SEED", os.path.expanduser("~/Downloads/sounds.db"))
_CAT_LOCK = threading.Lock()

if not os.path.exists(CATALOG_DB) and os.path.exists(CATALOG_SEED):
    shutil.copy(CATALOG_SEED, CATALOG_DB)        # first run: import the original DB
_CAT = sqlite3.connect(CATALOG_DB, check_same_thread=False)
_CAT.row_factory = sqlite3.Row
with _CAT_LOCK:
    _CAT.executescript("""
      CREATE TABLE IF NOT EXISTS sounds
        (id INTEGER PRIMARY KEY, command TEXT, file TEXT, nsfw INTEGER DEFAULT 0);
      CREATE TABLE IF NOT EXISTS sound_stats_all_time
        (soundid INTEGER, count INTEGER, last_update INTEGER);
      CREATE INDEX IF NOT EXISTS idx_sounds_file ON sounds(file);
      CREATE INDEX IF NOT EXISTS idx_stats_soundid ON sound_stats_all_time(soundid);
    """)
    _CAT.commit()

_CMD2FILE = {}   # command (lowercased) -> file   (curated aliases)
_FILE2ID  = {}   # file -> canonical soundid       (lowest id for that file)
_NSFW     = set()# files flagged nsfw
_PLAYS    = {}   # file -> all-time play count (summed across alias rows)

def catalog_sync():
    """Add any library files missing from the catalog so new sounds get tracked."""
    with _LIB_LOCK:
        files = list(_LIBRARY.keys())
    with _CAT_LOCK:
        have = {r["file"] for r in _CAT.execute("SELECT file FROM sounds")}
        for f in files:
            if f not in have:
                _CAT.execute("INSERT INTO sounds(command,file,nsfw) VALUES(?,?,0)",
                             (os.path.splitext(f)[0].lower(), f))
        _CAT.commit()
    catalog_reload()

def catalog_reload():
    global _CMD2FILE, _FILE2ID, _NSFW, _PLAYS
    with _CAT_LOCK:
        rows  = _CAT.execute("SELECT id,command,file,nsfw FROM sounds").fetchall()
        stats = {r["soundid"]: (r["count"] or 0)
                 for r in _CAT.execute("SELECT soundid,count FROM sound_stats_all_time")}
    c2f, f2id, nsfw = {}, {}, set()
    file_by_id = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        file_by_id[r["id"]] = r["file"]
        if r["file"] not in f2id:
            f2id[r["file"]] = r["id"]            # canonical id = lowest
        cmd = (r["command"] or "").strip().lower()
        if cmd and cmd not in c2f:
            c2f[cmd] = r["file"]
        if r["nsfw"]:
            nsfw.add(r["file"])
    plays = {}
    for sid, cnt in stats.items():               # sum stats across a file's alias rows
        f = file_by_id.get(sid)
        if f:
            plays[f] = plays.get(f, 0) + cnt
    _CMD2FILE, _FILE2ID, _NSFW, _PLAYS = c2f, f2id, nsfw, plays

def record_play(fn):
    sid = _FILE2ID.get(fn)
    if sid is None:
        return
    now = int(time.time())
    with _CAT_LOCK:
        if _CAT.execute("SELECT 1 FROM sound_stats_all_time WHERE soundid=?", (sid,)).fetchone():
            _CAT.execute("UPDATE sound_stats_all_time SET count=count+1,last_update=? WHERE soundid=?", (now, sid))
        else:
            _CAT.execute("INSERT INTO sound_stats_all_time(soundid,count,last_update) VALUES(?,1,?)", (sid, now))
        _CAT.commit()
    _PLAYS[fn] = _PLAYS.get(fn, 0) + 1

catalog_reload()

# ----------------------------------------------------------------------------
# Favorites & limits
# ----------------------------------------------------------------------------
def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

_FAV_ORDER = _dedup(_load(FAVS_FILE, []))          # ordered list of filenames
_FAVS = set(_FAV_ORDER)                            # fast membership
_lanes_cfg  = _load(LIMITS_FILE, {"lanes": 2, "song_lanes": 1})
_LANES      = max(1, min(4, int(_lanes_cfg.get("lanes", 2))))        # 1-4 short (sound) lanes
_SONG_LANES = max(1, min(2, int(_lanes_cfg.get("song_lanes", 1))))  # 1-2 long (song) lanes
_BOX_VOL = int(_load(BOXVOL_FILE, {"v": 50}).get("v", 50))  # kitchen box vol (admin)
_SYNC = bool(_load(SYNC_FILE, {"on": True}).get("on", True))  # global sync (admin), default on

def save_favs():    _save(FAVS_FILE, _FAV_ORDER)
def save_lanes():   _save(LIMITS_FILE, {"lanes": _LANES, "song_lanes": _SONG_LANES})
def save_boxvol():  _save(BOXVOL_FILE, {"v": _BOX_VOL})
def save_sync():    _save(SYNC_FILE, {"on": _SYNC})

# ----------------------------------------------------------------------------
# Active channels (the live "what's playing") + interrupt logic
# ----------------------------------------------------------------------------
_ACTIVE = []            # list of dicts: token, file, name, fmt, start, by, dur
_ACTIVE_LOCK = threading.Lock()
_TOKEN = 0

def _prune_locked(now):
    # keep a sound "active" until the slowest (synced) client has finished playing it,
    # so a finished sound is never force-stopped mid-tail. Interrupts/kills remove it
    # immediately regardless. pad covers the sync buffer + duration-estimate slack.
    pad = (SYNC_BUFFER if _SYNC else 0.0) + 0.6
    _ACTIVE[:] = [a for a in _ACTIVE if now < a["start"] + a["dur"] + pad]

def fire(fn, user, lane=0):
    """Play a sound in a lane. Each lane holds one sound — a new sound interrupts it."""
    global _TOKEN
    info = _LIBRARY.get(fn)
    if not info:
        return None
    dur = duration(fn)
    is_song = dur > LONG_THRESHOLD
    now = time.time()
    with _ACTIVE_LOCK:
        _prune_locked(now)
        if is_song:
            req = None                                  # explicit song lane the client picked?
            if isinstance(lane, str) and lane.startswith("song"):
                try:
                    idx = int(lane[4:])
                except ValueError:
                    idx = -1
                if 0 <= idx < _SONG_LANES:
                    req = f"song{idx}"
            if req is not None:                         # use that lane, interrupt it
                lane = req
                for a in [x for x in _ACTIVE if x.get("lane") == lane]:
                    _ACTIVE.remove(a)
            else:                                       # auto-fill an open lane, else override oldest
                songs = sorted([a for a in _ACTIVE if str(a.get("lane", "")).startswith("song")],
                               key=lambda a: a["start"])
                used = {a["lane"] for a in songs}
                lane = next((f"song{i}" for i in range(_SONG_LANES) if f"song{i}" not in used), None)
                if lane is None:
                    oldest = songs[0]; lane = oldest["lane"]; _ACTIVE.remove(oldest)
        else:
            try:
                lane = int(lane)
            except (TypeError, ValueError):
                lane = 0
            lane = max(0, min(_LANES - 1, lane))
            for a in [x for x in _ACTIVE if x.get("lane") == lane]:   # interrupt this lane
                _ACTIVE.remove(a)
        _TOKEN += 1
        entry = {
            "token": _TOKEN, "file": fn, "name": info["name"], "fmt": info["fmt"],
            "lane": lane, "start": now, "by": user, "dur": dur, "color": _USER_COLOR.get(user),
        }
        _ACTIVE.append(entry)
    record_play(fn)
    log_event("cmd", user, name=info["name"], file=fn, fmt=info["fmt"], lane=lane,
              color=_USER_COLOR.get(user))
    return entry

def active_snapshot():
    now = time.time()
    with _ACTIVE_LOCK:
        _prune_locked(now)
        snap = [dict(a) for a in _ACTIVE]
    with _LIB_LOCK:
        for a in snap:                       # carry the file version for cache-busting
            a["ver"] = _LIBRARY.get(a["file"], {}).get("ver", 0)
    return snap

# ----------------------------------------------------------------------------
# Unified feed (chat + commands + log)
# ----------------------------------------------------------------------------
_FEED = deque(maxlen=300)
_FEED_LOCK = threading.Lock()
_FEED_ID = 0

def _feed_add(kind, user, **extra):
    global _FEED_ID
    with _FEED_LOCK:
        _FEED_ID += 1
        item = {"id": _FEED_ID, "kind": kind, "user": user or "someone",
                "ts": time.time(), **extra}
        _FEED.append(item)
        return item

def log_event(kind, user, **extra):
    return _feed_add(kind, user, **extra)

def save_feed():
    now = time.time()
    with _FEED_LOCK:
        items = [i for i in _FEED if now - i.get("ts", 0) <= _FEED_TTL]
    try:
        _save(FEED_FILE, items)
    except Exception:
        pass

def load_feed():
    global _FEED_ID
    items = _load(FEED_FILE, [])
    now = time.time()
    items = [i for i in items if now - i.get("ts", 0) <= _FEED_TTL]
    with _FEED_LOCK:
        _FEED.clear(); _FEED.extend(items[-300:])
        _FEED_ID = max([i.get("id", 0) for i in _FEED], default=0)

def _persist_loop():
    while True:
        time.sleep(15)
        save_feed()

# ----------------------------------------------------------------------------
# Presence (who's online) — keyed by display name, refreshed via /api/active
# ----------------------------------------------------------------------------
_PRESENCE = {}                 # name -> last_seen ts
_PRESENCE_LOCK = threading.Lock()
_PRESENCE_TTL = 70

def presence_touch(name):
    if not name:
        return
    now = time.time()
    with _PRESENCE_LOCK:
        _PRESENCE[name] = now
        for n in [k for k, v in _PRESENCE.items() if now - v > _PRESENCE_TTL]:
            del _PRESENCE[n]

def presence_list():
    now = time.time()
    with _PRESENCE_LOCK:
        return sorted(n for n, v in _PRESENCE.items() if now - v <= _PRESENCE_TTL)

_USER_COLOR = {}                  # name -> "#rrggbb"
def _valid_color(c):
    return (isinstance(c, str) and len(c) == 7 and c[0] == "#"
            and all(ch in "0123456789abcdefABCDEF" for ch in c[1:]))
def set_color(name, color):
    if name and _valid_color(color):
        _USER_COLOR[name] = color

# ----------------------------------------------------------------------------
# Routes — pages
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

# ----------------------------------------------------------------------------
# Routes — auth
# ----------------------------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or {}
    pw = body.get("password") or ""
    email = (body.get("email") or "").strip()
    if pw:
        if pw == ADMIN_PASS:
            session.permanent = True
            session["admin"] = True; session["can_edit"] = True; session["play"] = True
            return jsonify({"ok": True, **me_dict()})
        if USER_PASS and pw == USER_PASS:                  # shared play-access password
            session.permanent = True
            session["play"] = True
            return jsonify({"ok": True, **me_dict()})
        return jsonify({"ok": False, "error": "wrong password"}), 401
    if email:
        session.permanent = True
        session["email"] = email[:80]; session["can_edit"] = True; session["play"] = True
        return jsonify({"ok": True, **me_dict()})
    return jsonify({"ok": False, "error": "enter a password or email"}), 400

@app.route("/api/logout", methods=["POST"])
def api_logout():
    for k in ("admin", "can_edit", "email", "play"):
        session.pop(k, None)
    return jsonify({"ok": True, **me_dict()})

@app.route("/api/me")
def api_me():
    return jsonify(me_dict())

# ----------------------------------------------------------------------------
# Routes — library
# ----------------------------------------------------------------------------
@app.route("/api/sounds")
def api_sounds():
    with _LIB_LOCK:
        items = list(_LIBRARY.values())
    items.sort(key=lambda x: x["name"].lower())
    favs = _FAVS
    out = [{**it, "fav": it["file"] in favs,
            "dur": round(_DUR.get(it["file"], 0), 1),
            "long": _DUR.get(it["file"], 0) > LONG_THRESHOLD,
            "nsfw": it["file"] in _NSFW,
            "plays": _PLAYS.get(it["file"], 0)} for it in items]
    fav_order = [f for f in _FAV_ORDER if f in _LIBRARY]
    return jsonify({"count": len(out), "sounds": out, "fav_order": fav_order,
                    "scanning": _DUR_SCANNING})

@app.route("/api/top")
def api_top():
    try:
        n = max(1, min(200, int(request.args.get("n", 50))))
    except (TypeError, ValueError):
        n = 50
    with _LIB_LOCK:
        lib = dict(_LIBRARY)
    items = [{"file": f, "name": lib[f]["name"], "plays": p,
              "long": _DUR.get(f, 0) > LONG_THRESHOLD, "nsfw": f in _NSFW}
             for f, p in _PLAYS.items() if f in lib and p > 0]
    items.sort(key=lambda x: -x["plays"])
    return jsonify({"top": items[:n]})

@app.route("/api/audio")
def api_audio():
    fn = request.args.get("f", "")
    if fn not in _LIBRARY:
        abort(404)
    full = os.path.join(SOUND_DIR, fn)
    if not os.path.isfile(full):
        abort(404)
    return send_file(full, conditional=True)

# ----------------------------------------------------------------------------
# Routes — firing / feed / chat
# ----------------------------------------------------------------------------
@app.route("/api/fire", methods=["POST"])
def api_fire():
    body = request.get_json(silent=True) or {}
    user = (body.get("user") or "someone").strip()[:40]
    set_color(user, body.get("color"))
    fn = body.get("file")
    # allow firing by !cmd too (typed path)
    if not fn and body.get("cmd"):
        c = body["cmd"].strip().lstrip("!").lower()
        # curated alias first (from the original DB), then filename match
        if _CMD2FILE.get(c) in _LIBRARY:
            fn = _CMD2FILE[c]
        matches = [] if fn else [f for f, i in _LIBRARY.items() if i["cmd"] == c]
        if not fn and not matches:
            matches = [f for f, i in _LIBRARY.items() if c in i["cmd"]]
            matches.sort(key=lambda f: (f not in _FAVS, len(f)))
        if not fn:
            fn = matches[0] if matches else None
    if not fn:
        return jsonify({"ok": False, "error": "not found"}), 404
    entry = fire(fn, user, body.get("lane", 0))
    if not entry:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "fired": entry, "active": active_snapshot()})

@app.route("/api/time")
def api_time():
    return jsonify({"t": time.time()})

@app.route("/api/active")
def api_active():
    _u = (request.args.get("u") or "").strip()[:40]
    presence_touch(_u)
    set_color(_u, request.args.get("c"))
    online = [{"name": n, "color": _USER_COLOR.get(n)} for n in presence_list()]
    return jsonify({"active": active_snapshot(), "lanes": _LANES, "song_lanes": _SONG_LANES,
                    "box_volume": _BOX_VOL, "sync": _SYNC, "online": online})

@app.route("/api/active/<int:token>/stop", methods=["POST"])
def api_active_stop(token):
    """Kill a playing sound. Allowed for an admin OR the person who triggered it."""
    body = request.get_json(silent=True) or {}
    user = (body.get("user") or "").strip()[:40]
    with _ACTIVE_LOCK:
        entry = next((a for a in _ACTIVE if a["token"] == token), None)
        if not entry:
            return jsonify({"ok": False}), 404
        if not (session.get("admin") or (user and entry.get("by") == user)):
            return jsonify({"ok": False, "error": "not allowed"}), 403
        _ACTIVE.remove(entry)
    return jsonify({"ok": True, "active": active_snapshot()})

@app.route("/api/sync", methods=["GET", "POST"])
def api_sync():
    global _SYNC
    if request.method == "POST":
        if not session.get("admin"):
            return jsonify({"ok": False, "error": "admin only"}), 403
        body = request.get_json(silent=True) or {}
        _SYNC = bool(body.get("on", _SYNC)); save_sync()
    return jsonify({"on": _SYNC})

@app.route("/api/box_volume", methods=["GET", "POST"])
def api_box_volume():
    global _BOX_VOL
    if request.method == "POST":
        if not session.get("admin"):
            return jsonify({"ok": False, "error": "admin only"}), 403
        body = request.get_json(silent=True) or {}
        try:
            _BOX_VOL = max(0, min(100, int(body.get("v", _BOX_VOL))))
            save_boxvol()
        except (TypeError, ValueError):
            pass
    return jsonify({"box_volume": _BOX_VOL})

@app.route("/api/chat", methods=["POST"])
def api_chat():
    body = request.get_json(silent=True) or {}
    user = (body.get("user") or "someone").strip()[:40]
    text = (body.get("text") or "").strip()[:280]
    if not text:
        return jsonify({"ok": False}), 400
    set_color(user, body.get("color"))
    item = _feed_add("chat", user, text=text, color=_USER_COLOR.get(user))
    save_feed()
    return jsonify({"ok": True, "item": item})

@app.route("/api/feed")
def api_feed():
    since = int(request.args.get("since", "0") or 0)
    with _FEED_LOCK:
        items = [i for i in _FEED if i["id"] > since]
    return jsonify({"feed": items})

# ----------------------------------------------------------------------------
# Routes — favorites & limits
# ----------------------------------------------------------------------------
@app.route("/api/favorite", methods=["POST"])
def api_favorite():
    body = request.get_json(silent=True) or {}
    fn = body.get("file")
    if fn not in _LIBRARY:
        return jsonify({"ok": False}), 404
    if body.get("on"):
        if fn not in _FAVS:
            _FAVS.add(fn); _FAV_ORDER.append(fn)
    else:
        _FAVS.discard(fn)
        if fn in _FAV_ORDER:
            _FAV_ORDER.remove(fn)
    save_favs()
    return jsonify({"ok": True, "fav": fn in _FAVS})

@app.route("/api/favorites/order", methods=["POST"])
def api_fav_order():
    body = request.get_json(silent=True) or {}
    order = _dedup(body.get("order") or [])
    new = [f for f in order if f in _FAVS]          # only real favorites
    for f in _FAV_ORDER:                            # keep any not mentioned
        if f not in new:
            new.append(f)
    _FAV_ORDER[:] = new
    save_favs()
    return jsonify({"ok": True, "order": _FAV_ORDER})

@app.route("/api/lanes", methods=["GET", "POST"])
def api_lanes():
    global _LANES, _SONG_LANES
    if request.method == "POST":
        if not session.get("admin"):
            return jsonify({"ok": False, "error": "admin only"}), 403
        body = request.get_json(silent=True) or {}
        try:
            if "lanes" in body:
                _LANES = max(1, min(4, int(body["lanes"])))
            if "song_lanes" in body:
                _SONG_LANES = max(1, min(2, int(body["song_lanes"])))
            save_lanes()
            def _keep(a):
                L = a.get("lane")
                if isinstance(L, int):
                    return L < _LANES
                if isinstance(L, str) and L.startswith("song"):
                    try:
                        return int(L[4:]) < _SONG_LANES
                    except ValueError:
                        return False
                return True
            with _ACTIVE_LOCK:
                _ACTIVE[:] = [a for a in _ACTIVE if _keep(a)]
        except (TypeError, ValueError):
            pass
    return jsonify({"lanes": _LANES, "song_lanes": _SONG_LANES})

# ----------------------------------------------------------------------------
# Routes — Add / Edit
# ----------------------------------------------------------------------------
@app.route("/api/upload", methods=["POST"])
def api_upload():
    if not can_edit():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "no file"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in AUDIO_EXTS:
        return jsonify({"ok": False, "error": "only .wav/.mp3"}), 400
    name = secure_filename(f.filename)
    dest = os.path.join(SOUND_DIR, name)
    if os.path.exists(dest):
        return jsonify({"ok": False, "error": "name exists"}), 409
    f.save(dest)
    scan_library()
    return jsonify({"ok": True, "file": name})

@app.route("/api/fetch_url", methods=["POST"])
def api_fetch_url():
    """Download audio from a link (yt-dlp) into the library. Same gate as Add."""
    if not can_edit():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if not YTDLP:
        return jsonify({"ok": False, "error": "yt-dlp not installed"}), 500
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "no url"}), 400
    name = secure_filename((body.get("name") or "").strip())
    fmt = (body.get("format") or "mp3").lower()
    if fmt not in ("mp3", "wav"):
        fmt = "mp3"
    out = os.path.join(SOUND_DIR, (name + ".%(ext)s") if name else "%(title).70s.%(ext)s")
    cmd = [YTDLP, "--no-playlist", "--restrict-filenames",
           "-x", "--audio-format", fmt, "-o", out, url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if r.returncode != 0:
        return jsonify({"ok": False, "error": (r.stderr or "download failed")[-300:]}), 500
    scan_library()
    return jsonify({"ok": True})

@app.route("/api/rename", methods=["POST"])
def api_rename():
    if not can_edit():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    old = body.get("file")
    newstem = secure_filename((body.get("name") or "").strip())
    if old not in _LIBRARY or not newstem:
        return jsonify({"ok": False}), 400
    ext = os.path.splitext(old)[1]
    new = newstem + ext
    src = os.path.join(SOUND_DIR, old)
    dst = os.path.join(SOUND_DIR, new)
    if os.path.exists(dst):
        return jsonify({"ok": False, "error": "name exists"}), 409
    os.rename(src, dst)
    if old in _FAVS:
        _FAVS.discard(old); _FAVS.add(new)
        _FAV_ORDER[:] = [new if f == old else f for f in _FAV_ORDER]
        save_favs()
    scan_library()
    return jsonify({"ok": True, "file": new})

@app.route("/api/delete", methods=["POST"])
def api_delete():
    if not can_edit():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    fn = body.get("file")
    if fn not in _LIBRARY:
        return jsonify({"ok": False}), 404
    try:
        os.remove(os.path.join(SOUND_DIR, fn))
    except OSError:
        return jsonify({"ok": False}), 500
    _FAVS.discard(fn)
    if fn in _FAV_ORDER:
        _FAV_ORDER.remove(fn)
    save_favs()
    scan_library()
    return jsonify({"ok": True})

@app.route("/api/edit", methods=["POST"])
def api_edit():
    """In-place trim + volume (overwrites the file). Same gate as Add."""
    if not can_edit():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    fn = body.get("file")
    if fn not in _LIBRARY:
        return jsonify({"ok": False}), 404
    try:
        start = max(0.0, float(body.get("start", 0)))
        end   = float(body.get("end", 0))
        gain  = float(body.get("gain", 1.0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad params"}), 400
    dur = duration(fn)
    if end <= 0 or end > dur:
        end = dur
    if end - start < 0.05:
        return jsonify({"ok": False, "error": "selection too short"}), 400
    gain = max(0.0, min(8.0, gain))
    full = os.path.join(SOUND_DIR, fn)
    ext  = os.path.splitext(fn)[1].lower()
    tmp  = full + ".edit" + ext
    cmd = ["ffmpeg", "-y", "-i", full, "-ss", str(start), "-t", str(end - start)]
    if abs(gain - 1.0) > 0.001:
        cmd += ["-af", "volume=" + str(round(gain, 3))]
    cmd += [tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if r.returncode != 0 or not os.path.exists(tmp):
        return jsonify({"ok": False, "error": "ffmpeg failed"}), 500
    os.replace(tmp, full)
    with _DUR_LOCK:                       # invalidate cached duration
        _DUR.pop(fn, None); _save(DUR_FILE, _DUR)
    scan_library()
    return jsonify({"ok": True})

# ----------------------------------------------------------------------------
if __name__ == "__main__":
    n = len(scan_library())
    catalog_sync()
    load_feed()
    threading.Thread(target=_persist_loop, daemon=True).start()
    threading.Thread(target=probe_all_durations, daemon=True).start()
    print(f"Sound Server — {n} sounds from {SOUND_DIR}")
    print(f"  http://localhost:{PORT}")
    try:
        from waitress import serve            # production-grade, threaded, single-process
        serve(app, host="0.0.0.0", port=PORT, threads=16, channel_timeout=30)
    except ImportError:
        app.run(host="0.0.0.0", port=PORT, threaded=True)   # dev fallback
