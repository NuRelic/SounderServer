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
import gzip
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
TYPE_OVERRIDE_FILE = os.path.join(DATA_DIR, "type_overrides.json")
SYNC_BUFFER = 1.0                       # must match the frontend sync buffer
FAVS_FILE  = os.path.join(DATA_DIR, "favorites.json")
LIMITS_FILE= os.path.join(DATA_DIR, "limits.json")
DUR_FILE   = os.path.join(DATA_DIR, "durations.json")
BOXVOL_FILE= os.path.join(DATA_DIR, "box_volume.json")
FEED_FILE  = os.path.join(DATA_DIR, "feed_store.json")
SYNC_FILE  = os.path.join(DATA_DIR, "sync.json")
COLOR_FILE = os.path.join(DATA_DIR, "user_colors.json")
_FEED_TTL  = 3 * 86400                              # keep feed 3 days
YTDLP      = shutil.which("yt-dlp")

os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)
from recipes import recipes_bp
app.register_blueprint(recipes_bp)

# ----------------------------------------------------------------------------
# Sessions / auth (staging)
#   Listening is always open — there is no access wall. Login only grants edit
#   tiers: USER_PASS -> can_edit (add / edit clips); ADMIN_PASS -> can_edit +
#   admin (also remove clips and change lanes / sync / box volume).
# ----------------------------------------------------------------------------
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin")   # add/edit + remove
USER_PASS  = os.environ.get("USER_PASS", "")          # add/edit only (set via env)
_SECRET_FILE = os.path.join(DATA_DIR, ".flask_secret")
if os.path.exists(_SECRET_FILE):
    app.secret_key = open(_SECRET_FILE, "rb").read()
else:
    app.secret_key = _secrets.token_bytes(32)
    with open(_SECRET_FILE, "wb") as _f:
        _f.write(app.secret_key)
app.permanent_session_lifetime = timedelta(days=30)

@app.after_request
def _gzip_json(resp):
    # gzip JSON bodies (highly compressible, ~85%) so /api/sounds (~0.5MB) and the
    # constant /api/active + /api/feed polls ship a fraction of the bytes. Audio is
    # already-compressed media (and uses send_file/range) so we never touch it.
    try:
        if (resp.mimetype == "application/json"
                and "gzip" in request.headers.get("Accept-Encoding", "")
                and resp.status_code == 200
                and not resp.direct_passthrough
                and "Content-Encoding" not in resp.headers):
            data = resp.get_data()
            if len(data) > 500:
                resp.set_data(gzip.compress(data, 5))
                resp.headers["Content-Encoding"] = "gzip"
                resp.headers["Vary"] = "Accept-Encoding"
    except Exception:
        pass
    return resp

def can_edit():
    return bool(session.get("admin") or session.get("can_edit"))

def me_dict():
    # listening is open to everyone; these two tiers gate add/edit and remove
    return {"admin": bool(session.get("admin")),
            "can_edit": can_edit()}

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
_LIBRARY = {}   # filename -> {"file","name","cmd","fmt","ver"}
_SOUNDS_SORTED = None   # cached name-sorted base list for /api/sounds; None = rebuild

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
    global _SOUNDS_SORTED
    with _LIB_LOCK:
        _LIBRARY.clear()
        _LIBRARY.update(lib)
        _SOUNDS_SORTED = None        # library changed → drop the cached sort
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
        snap = dict(_DUR)          # snapshot under the lock…
    _save(DUR_FILE, snap)          # …but write to disk WITHOUT holding it
    return d

_TYPE_OVERRIDE = _load(TYPE_OVERRIDE_FILE, {})   # {file: "song"|"sound"}

def is_long(fn):
    """Effective song/sound classification: per-file override beats the 15s rule."""
    ov = _TYPE_OVERRIDE.get(fn)
    if ov == "song":  return True
    if ov == "sound": return False
    return duration(fn) > LONG_THRESHOLD

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
        # A sound added since startup (upload / fetch_url) is in the library but has
        # no catalog row yet — catalog_sync() only runs at boot. Give it a soundid on
        # the fly so its plays are counted instead of silently dropped.
        if fn not in _LIBRARY:
            return
        with _CAT_LOCK:
            row = _CAT.execute("SELECT id FROM sounds WHERE file=? ORDER BY id LIMIT 1",
                               (fn,)).fetchone()
            if row:
                sid = row["id"]
            else:
                sid = _CAT.execute("INSERT INTO sounds(command,file,nsfw) VALUES(?,?,0)",
                                   (os.path.splitext(fn)[0].lower(), fn)).lastrowid
            _CAT.commit()
        _FILE2ID[fn] = sid
    now = int(time.time())
    with _CAT_LOCK:
        if _CAT.execute("SELECT 1 FROM sound_stats_all_time WHERE soundid=?", (sid,)).fetchone():
            _CAT.execute("UPDATE sound_stats_all_time SET count=count+1,last_update=? WHERE soundid=?", (now, sid))
        else:
            _CAT.execute("INSERT INTO sound_stats_all_time(soundid,count,last_update) VALUES(?,1,?)", (sid, now))
        _CAT.commit()
    _PLAYS[fn] = _PLAYS.get(fn, 0) + 1

def catalog_rename(old, new):
    """Repoint a renamed file in the catalog so its soundid — and play stats — carry over."""
    with _CAT_LOCK:
        _CAT.execute("UPDATE sounds SET file=? WHERE file=?", (new, old))
        _CAT.commit()
    catalog_reload()

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

# per-user favorites: {key(username): {"favs":[...], "deck":[...]}}. Migrate an
# old global list (single shared favorites) → the admin (Banandon) identity.
def _norm_fav(v):
    """Normalize stored favorites to {'favs':[...], 'deck':[...]} (list = legacy migration)."""
    if isinstance(v, list):
        return {"favs": _dedup([f for f in v if isinstance(f, str)]), "deck": []}
    if isinstance(v, dict):
        favs = _dedup([f for f in v.get("favs", []) if isinstance(f, str)])
        deck = _dedup([f for f in v.get("deck", []) if isinstance(f, str) and f in favs])
        return {"favs": favs, "deck": deck}
    return {"favs": [], "deck": []}

_raw_favs = _load(FAVS_FILE, {})
if isinstance(_raw_favs, list):
    _raw_favs = {"banandon": _raw_favs}
_FAVS_BY_USER = {k: _norm_fav(v) for k, v in (_raw_favs or {}).items()}

def fav_key(user):
    return ((user or "").strip().lower()[:40]) or "anon"
def user_fav_rec(user):
    return _FAVS_BY_USER.setdefault(fav_key(user), {"favs": [], "deck": []})
_lanes_cfg  = _load(LIMITS_FILE, {"lanes": 2, "song_lanes": 1})
_LANES      = max(1, min(4, int(_lanes_cfg.get("lanes", 2))))        # 1-4 short (sound) lanes
_SONG_LANES = max(1, min(2, int(_lanes_cfg.get("song_lanes", 1))))  # 1-2 long (song) lanes
_BOX_VOL = int(_load(BOXVOL_FILE, {"v": 50}).get("v", 50))  # kitchen box vol (admin)
_SYNC = bool(_load(SYNC_FILE, {"on": True}).get("on", True))  # global sync (admin), default on

def save_favs():    _save(FAVS_FILE, _FAVS_BY_USER)
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
    is_song = is_long(fn)
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
        save_colors()

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

_USER_COLOR = _load(COLOR_FILE, {})   # name -> "#rrggbb" (persisted across restarts)
if not isinstance(_USER_COLOR, dict):
    _USER_COLOR = {}
def _valid_color(c):
    return (isinstance(c, str) and len(c) == 7 and c[0] == "#"
            and all(ch in "0123456789abcdefABCDEF" for ch in c[1:]))
def set_color(name, color):
    if name and _valid_color(color):
        _USER_COLOR[name] = color
def save_colors():
    try: _save(COLOR_FILE, dict(_USER_COLOR))
    except Exception: pass

# ----------------------------------------------------------------------------
# Routes — pages
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", sync_buffer=SYNC_BUFFER)

# ----------------------------------------------------------------------------
# Routes — auth
# ----------------------------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or {}
    pw = body.get("password") or ""
    if pw == ADMIN_PASS:                        # admin: add/edit + remove
        session.permanent = True
        session["admin"] = True; session["can_edit"] = True
        return jsonify({"ok": True, **me_dict()})
    if USER_PASS and pw == USER_PASS:          # editor: add/edit (cannot remove)
        session.permanent = True
        session["can_edit"] = True
        session.pop("admin", None)
        return jsonify({"ok": True, **me_dict()})
    return jsonify({"ok": False, "error": "wrong password"}), 401

@app.route("/api/logout", methods=["POST"])
def api_logout():
    for k in ("admin", "can_edit"):
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
    global _SOUNDS_SORTED
    items = _SOUNDS_SORTED
    if items is None:                      # rebuild the name-sort only when the library changed
        with _LIB_LOCK:
            items = sorted(_LIBRARY.values(), key=lambda x: x["name"].lower())
        _SOUNDS_SORTED = items
    with _DUR_LOCK:                         # snapshot under the lock to avoid races with the probe
        dur = dict(_DUR)
    nsfw, plays = _NSFW, _PLAYS
    # favorites are per-user now — the client fetches them from /api/favorites?user=
    out = [{**it,
            "dur": round(dur.get(it["file"], 0), 1),
            "long": is_long(it["file"]),
            "nsfw": it["file"] in nsfw,
            "plays": plays.get(it["file"], 0)} for it in items]
    return jsonify({"count": len(out), "sounds": out, "scanning": _DUR_SCANNING})

@app.route("/api/audio")
def api_audio():
    fn = request.args.get("f", "")
    if fn not in _LIBRARY:
        abort(404)
    full = os.path.join(SOUND_DIR, fn)
    if not os.path.isfile(full):
        abort(404)
    resp = send_file(full, conditional=True)
    # When the URL carries a version (v=mtime, set by audioUrl/active_snapshot), the
    # content at that URL is immutable — an edit changes mtime → a new URL. So we can
    # cache it hard and skip the per-fire revalidation round-trip every client + the
    # Pi would otherwise make. Version-less requests keep the default revalidation.
    if request.args.get("v"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp

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
            matches.sort(key=len)        # shortest cmd match wins (favorites are per-user now)
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
    # no synchronous save here — the 15s _persist_loop already flushes the feed,
    # so the chat hot path skips a full-feed JSON serialize + disk write per message
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
@app.route("/api/favorites")
def api_favorites_get():
    """Return one user's favorites + deck, filtered to files still in the library."""
    rec = user_fav_rec(request.args.get("user", ""))
    with _LIB_LOCK:
        favs = [f for f in rec["favs"] if f in _LIBRARY]
        deck = [f for f in rec["deck"] if f in _LIBRARY and f in rec["favs"]]
    return jsonify({"favs": favs, "deck": deck})

@app.route("/api/favorite", methods=["POST"])
def api_favorite():
    body = request.get_json(silent=True) or {}
    fn = body.get("file")
    if fn not in _LIBRARY:
        return jsonify({"ok": False}), 404
    rec = user_fav_rec(body.get("user"))
    if body.get("on"):
        if fn not in rec["favs"]:
            rec["favs"].append(fn)              # new favorites land in the "rest", not the deck
    else:
        rec["favs"] = [f for f in rec["favs"] if f != fn]
        rec["deck"] = [f for f in rec["deck"] if f != fn]
    save_favs()
    return jsonify({"ok": True, "fav": fn in rec["favs"]})

@app.route("/api/favorites/order", methods=["POST"])
def api_fav_order():
    """Set the user's deck: an ordered subset of their favorites."""
    body = request.get_json(silent=True) or {}
    rec = user_fav_rec(body.get("user"))
    order = _dedup(body.get("order") or [])
    rec["deck"] = [f for f in order if f in rec["favs"]]
    save_favs()
    return jsonify({"ok": True, "deck": rec["deck"]})

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

# ---------------------------------------------------------------------------
# URL downloads via a job queue. A worker (the Pi — residential IP, not
# bot-gated) polls /api/worker/claim, downloads, and POSTs the file back. If no
# worker claims a job in time, the VPS downloads it locally (best-effort).
# Fetches must never block the HTTP request (Cloudflare kills it at ~100s).
# ---------------------------------------------------------------------------
_FETCH_JOBS = {}            # id -> {id,url,fmt,name,status,error,file,ts,claim_ts}
_FETCH_LOCK = threading.Lock()
_FETCH_NEXT = [1]
FALLBACK_PENDING_SECS = 25  # no worker claimed it → VPS tries locally
FALLBACK_CLAIM_SECS   = 200 # worker claimed but never finished → VPS tries locally

WORKER_TOKEN_FILE = os.path.join(DATA_DIR, "worker_token")
WORKER_TOKEN = (os.environ.get("WORKER_TOKEN") or "").strip()
if not WORKER_TOKEN:
    try:
        WORKER_TOKEN = open(WORKER_TOKEN_FILE).read().strip()
    except OSError:
        WORKER_TOKEN = _secrets.token_hex(24)
        try:
            with open(WORKER_TOKEN_FILE, "w") as _f: _f.write(WORKER_TOKEN)
        except OSError: pass

def _check_worker():
    tok = request.headers.get("X-Worker-Token", "")
    return bool(WORKER_TOKEN) and _secrets.compare_digest(tok, WORKER_TOKEN)

DENO_PATH = os.path.expanduser("~/.deno/bin/deno")   # JS runtime for full YouTube extraction (x86_64 VPS)

def _ytdlp_cmd(url, fmt, name):
    out = os.path.join(SOUND_DIR, (name + ".%(ext)s") if name else "%(title).70s.%(ext)s")
    cmd = [YTDLP, "--no-playlist", "--restrict-filenames", "-x", "--audio-format", fmt,
           "--extractor-args", "youtube:player_client=default,tv", "-o", out]
    if os.path.isfile(DENO_PATH):                    # use the JS runtime when present (web client)
        cmd += ["--js-runtimes", "deno:" + DENO_PATH]
    ck = os.path.join(DATA_DIR, "yt_cookies.txt")
    if os.path.isfile(ck): cmd += ["--cookies", ck]
    cmd.append(url)
    return cmd

def _gate_msg(stderr):
    err = (stderr or "download failed"); low = err.lower()
    if any(s in low for s in ("sign in to confirm", "confirm you", "not a bot", "cookies")):
        return ("This video is sign-in gated by YouTube. Try again shortly — if it keeps "
                "failing it may need a cookies file (data/yt_cookies.txt).")
    return err[-300:]

def _set_job(jid, **kw):
    with _FETCH_LOCK:
        if jid in _FETCH_JOBS: _FETCH_JOBS[jid].update(**kw)

def _run_local(jid):
    with _FETCH_LOCK:
        j = _FETCH_JOBS.get(jid)
        if not j or j["status"] in ("done", "error", "local"): return
        j["status"] = "local"; j["claim_ts"] = time.time()
        url, fmt, name = j["url"], j["fmt"], j["name"]
    if not YTDLP:
        _set_job(jid, status="error", error="no downloader available"); return
    try:
        before = set(os.listdir(SOUND_DIR))
        r = subprocess.run(_ytdlp_cmd(url, fmt, name), capture_output=True, text=True, timeout=600)
    except Exception as e:
        _set_job(jid, status="error", error=str(e)); return
    if r.returncode != 0:
        _set_job(jid, status="error", error=_gate_msg(r.stderr)); return
    new = [f for f in (set(os.listdir(SOUND_DIR)) - before) if f.lower().endswith((".mp3", ".wav"))]
    scan_library()
    _set_job(jid, status="done", file=(new[0] if new else None))

def _fallback_loop():
    while True:
        time.sleep(5)
        now = time.time(); due = []
        with _FETCH_LOCK:
            for jid, j in _FETCH_JOBS.items():
                if j["status"] == "pending" and now - j["ts"] > FALLBACK_PENDING_SECS:
                    due.append(jid)
                elif j["status"] == "claimed" and now - j.get("claim_ts", now) > FALLBACK_CLAIM_SECS:
                    due.append(jid)
        for jid in due:
            threading.Thread(target=_run_local, args=(jid,), daemon=True).start()
threading.Thread(target=_fallback_loop, daemon=True).start()

@app.route("/api/fetch_url", methods=["POST"])
def api_fetch_url():
    """Enqueue a URL download (Pi worker, or VPS fallback). Client polls /api/fetch_status."""
    if not can_edit():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "no url"}), 400
    name = secure_filename((body.get("name") or "").strip())
    fmt = (body.get("format") or "mp3").lower()
    if fmt not in ("mp3", "wav"): fmt = "mp3"
    with _FETCH_LOCK:
        jid = _FETCH_NEXT[0]; _FETCH_NEXT[0] += 1
        _FETCH_JOBS[jid] = {"id": jid, "url": url, "fmt": fmt, "name": name,
                            "status": "pending", "error": None, "file": None,
                            "ts": time.time(), "claim_ts": 0}
        if len(_FETCH_JOBS) > 60:                       # keep the map small
            for k in sorted(_FETCH_JOBS)[:-60]: _FETCH_JOBS.pop(k, None)
    return jsonify({"ok": True, "job": jid})

@app.route("/api/fetch_status")
def api_fetch_status():
    try: jid = int(request.args.get("id", "0"))
    except (TypeError, ValueError): jid = 0
    with _FETCH_LOCK:
        j = _FETCH_JOBS.get(jid)
        if not j: return jsonify({"status": "unknown"})
        return jsonify({"status": j["status"], "error": j["error"], "file": j["file"]})

# ---- worker (Pi) endpoints — shared-token auth, all worker-initiated ----
@app.route("/api/worker/claim")
def api_worker_claim():
    if not _check_worker(): return jsonify({"error": "forbidden"}), 403
    with _FETCH_LOCK:
        cand = sorted([j for j in _FETCH_JOBS.values() if j["status"] == "pending"], key=lambda j: j["ts"])
        if not cand: return jsonify({"job": None})
        j = cand[0]; j["status"] = "claimed"; j["claim_ts"] = time.time()
        return jsonify({"job": {"id": j["id"], "url": j["url"], "fmt": j["fmt"], "name": j["name"]}})

@app.route("/api/worker/result/<int:jid>", methods=["POST"])
def api_worker_result(jid):
    if not _check_worker(): return jsonify({"error": "forbidden"}), 403
    f = request.files.get("file")
    if not f: return jsonify({"ok": False, "error": "no file"}), 400
    fn = secure_filename(f.filename or "")
    base, ext = os.path.splitext(fn)
    if ext.lower() not in (".mp3", ".wav") or not base:
        return jsonify({"ok": False, "error": "bad file"}), 400
    dest = os.path.join(SOUND_DIR, fn); n = 1
    while os.path.exists(dest):
        dest = os.path.join(SOUND_DIR, "%s_%d%s" % (base, n, ext)); n += 1
    f.save(dest)
    scan_library()
    _set_job(jid, status="done", file=os.path.basename(dest))
    return jsonify({"ok": True, "file": os.path.basename(dest)})

@app.route("/api/worker/fail/<int:jid>", methods=["POST"])
def api_worker_fail(jid):
    if not _check_worker(): return jsonify({"error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    # the Pi (residential) couldn't get it (often: needs a JS runtime it can't run) —
    # retry on the VPS, which has Deno, before giving up. _run_local sets the final state.
    _set_job(jid, error=_gate_msg(body.get("error", "")))   # interim: stash the worker's reason
    threading.Thread(target=_run_local, args=(jid,), daemon=True).start()
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
    changed = False                       # repoint this file in every user's favorites + deck
    for rec in _FAVS_BY_USER.values():
        for k in ("favs", "deck"):
            if old in rec[k]:
                rec[k] = [new if f == old else f for f in rec[k]]; changed = True
    if changed: save_favs()
    catalog_rename(old, new)              # carry the soundid (and its play stats) to the new name
    with _DUR_LOCK:                       # move the cached duration so song/sound stays stable
        if old in _DUR:
            _DUR[new] = _DUR.pop(old); _save(DUR_FILE, _DUR)
    scan_library()
    return jsonify({"ok": True, "file": new})

@app.route("/api/delete", methods=["POST"])
def api_delete():
    if not session.get("admin"):              # removing clips is admin-only
        return jsonify({"ok": False, "error": "forbidden — admin only"}), 403
    body = request.get_json(silent=True) or {}
    fn = body.get("file")
    if fn not in _LIBRARY:
        return jsonify({"ok": False}), 404
    try:
        os.remove(os.path.join(SOUND_DIR, fn))
    except OSError:
        return jsonify({"ok": False}), 500
    changed = False                       # drop the deleted file from every user's favorites + deck
    for rec in _FAVS_BY_USER.values():
        for k in ("favs", "deck"):
            if fn in rec[k]:
                rec[k] = [f for f in rec[k] if f != fn]; changed = True
    if changed: save_favs()
    scan_library()
    return jsonify({"ok": True})

@app.route("/api/edit", methods=["POST"])
def api_edit():
    """Trim + volume. Overwrites in place, or with new_name saves a copy as a new clip. Same gate as Add."""
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
    new_name = (body.get("new_name") or "").strip()   # non-empty => save a copy, keep the original
    if new_name:
        stem = secure_filename(new_name)
        if not stem:
            return jsonify({"ok": False, "error": "bad name"}), 400
        dest_fn = stem + ext
        out = os.path.join(SOUND_DIR, dest_fn); k = 1
        while os.path.exists(out):                     # never clobber an existing clip
            dest_fn = "%s_%d%s" % (stem, k, ext); out = os.path.join(SOUND_DIR, dest_fn); k += 1
    else:
        out = full + ".edit" + ext                     # trim to a temp file, then atomically replace
    cmd = ["ffmpeg", "-y", "-i", full, "-ss", str(start), "-t", str(end - start)]
    if abs(gain - 1.0) > 0.001:
        cmd += ["-af", "volume=" + str(round(gain, 3))]
    cmd += [out]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if r.returncode != 0 or not os.path.exists(out):
        return jsonify({"ok": False, "error": "ffmpeg failed"}), 500
    if new_name:                          # copy saved as a brand-new clip; original left alone
        scan_library()
        return jsonify({"ok": True, "file": dest_fn})
    os.replace(out, full)
    with _DUR_LOCK:                       # invalidate cached duration
        _DUR.pop(fn, None); _save(DUR_FILE, _DUR)
    scan_library()
    return jsonify({"ok": True})

@app.route("/api/sound_type", methods=["POST"])
def api_sound_type():
    """Force a clip to song/sound (or auto = 15s rule). Same gate as Add/Edit; shared/global."""
    if not can_edit():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    fn = body.get("file"); typ = body.get("type")
    if fn not in _LIBRARY:
        return jsonify({"ok": False}), 404
    if typ not in ("auto", "song", "sound"):
        return jsonify({"ok": False, "error": "bad type"}), 400
    if typ == "auto":
        _TYPE_OVERRIDE.pop(fn, None)
    else:
        _TYPE_OVERRIDE[fn] = typ
    _save(TYPE_OVERRIDE_FILE, _TYPE_OVERRIDE)
    return jsonify({"ok": True, "type": typ, "long": is_long(fn)})

# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# Stash board feedback bridge (operator clicks on the board -> queue -> the repo
# machine's pull_feedback loop drains + applies via board_io.py). Mirrors the
# worker-token pattern: a shared token (auto-generated to DATA_DIR on first run),
# checked with compare_digest via the X-Stash-Token header. Additive only.
# ----------------------------------------------------------------------------
STASH_FB_FILE = os.path.join(DATA_DIR, "stash_feedback.json")
STASH_FB_TOKEN_FILE = os.path.join(DATA_DIR, "stash_feedback_token")
STASH_FB_TOKEN = (os.environ.get("STASH_FEEDBACK_TOKEN") or "").strip()
if not STASH_FB_TOKEN:
    try:
        STASH_FB_TOKEN = open(STASH_FB_TOKEN_FILE).read().strip()
    except OSError:
        STASH_FB_TOKEN = _secrets.token_hex(24)
        with open(STASH_FB_TOKEN_FILE, "w") as _f:
            _f.write(STASH_FB_TOKEN)
_STASH_FB_LOCK = threading.Lock()


def _check_stash():
    tok = request.headers.get("X-Stash-Token", "")
    return bool(STASH_FB_TOKEN) and _secrets.compare_digest(tok, STASH_FB_TOKEN)


def _stash_load():
    try:
        with open(STASH_FB_FILE) as _f:
            return json.load(_f)
    except (OSError, ValueError):
        return []


def _stash_save(items):
    tmp = STASH_FB_FILE + ".tmp"
    with open(tmp, "w") as _f:
        json.dump(items, _f)
    os.replace(tmp, STASH_FB_FILE)


@app.route("/api/stash/feedback", methods=["POST"])
def api_stash_feedback():
    """Queue one operator intent from the board page. action in answer|ok|no."""
    if not _check_stash():
        return jsonify({"ok": False, "error": "bad token"}), 403
    body = request.get_json(silent=True) or {}
    ref = str(body.get("ref") or "").strip()[:12]
    action = str(body.get("action") or "").strip().lower()
    note = str(body.get("note") or "").strip()[:500]
    if not ref or action not in ("answer", "ok", "no"):
        return jsonify({"ok": False, "error": "need ref + action in answer|ok|no"}), 400
    if action in ("answer", "no") and not note:
        return jsonify({"ok": False, "error": "action %s needs a note" % action}), 400
    with _STASH_FB_LOCK:
        items = _stash_load()
        items.append({"ref": ref, "action": action, "note": note, "ts": time.time()})
        _stash_save(items)
        n = len(items)
    return jsonify({"ok": True, "queued": n})


@app.route("/api/stash/feedback/drain")
def api_stash_drain():
    """The repo machine pulls + clears the queue (GET, token-gated). Returns items[]."""
    if not _check_stash():
        return jsonify({"ok": False, "error": "bad token"}), 403
    with _STASH_FB_LOCK:
        items = _stash_load()
        _stash_save([])
    return jsonify({"ok": True, "items": items})


# ---------------------------------------------------------------------------
# Content review annotations (abilities / consumables / relics / events pages).
# Persistent per-page store keyed by item_id (last-write-wins), token-gated with
# the same X-Stash-Token secret as the stash feedback queue above.
#   POST /api/review/<page>  {item_id, status, note, rarity_override}  -> upsert
#   GET  /api/review/<page>                                            -> {items:{id:{...}}}
# ---------------------------------------------------------------------------
REVIEW_DIR = os.path.join(DATA_DIR, "reviews")
_REVIEW_LOCK = threading.Lock()
_REVIEW_PAGES = {"abilities", "consumables", "relics", "events", "classes"}


def _review_path(page):
    if page not in _REVIEW_PAGES:
        return None
    os.makedirs(REVIEW_DIR, exist_ok=True)
    return os.path.join(REVIEW_DIR, page + ".json")


def _review_load(page):
    p = _review_path(page)
    if not p:
        return None
    try:
        with open(p) as _f:
            return json.load(_f)
    except (OSError, ValueError):
        return {}


def _review_save(page, data):
    p = _review_path(page)
    tmp = p + ".tmp"
    with open(tmp, "w") as _f:
        json.dump(data, _f)
    os.replace(tmp, p)


@app.route("/api/review/<page>", methods=["GET"])
def api_review_get(page):
    if not _check_stash():
        return jsonify({"ok": False, "error": "bad token"}), 403
    with _REVIEW_LOCK:
        items = _review_load(page)
    if items is None:
        return jsonify({"ok": False, "error": "unknown page"}), 404
    return jsonify({"ok": True, "items": items})


@app.route("/api/review/<page>", methods=["POST"])
def api_review_post(page):
    if not _check_stash():
        return jsonify({"ok": False, "error": "bad token"}), 403
    if page not in _REVIEW_PAGES:
        return jsonify({"ok": False, "error": "unknown page"}), 404
    body = request.get_json(silent=True) or {}
    iid = str(body.get("item_id") or "").strip()[:200]
    if not iid:
        return jsonify({"ok": False, "error": "need item_id"}), 400
    status = str(body.get("status") or "open").strip().lower()
    if status not in ("open", "revise", "lock"):
        status = "open"
    entry = {
        "status": status,
        "note": str(body.get("note") or "").strip()[:2000],
        "rarity": str(body.get("rarity_override") or "").strip()[:24],
        "ts": time.time(),
    }
    with _REVIEW_LOCK:
        items = _review_load(page)
        if items is None:
            return jsonify({"ok": False, "error": "unknown page"}), 404
        # Drop an item back to default (open, no note, no override) -> remove it.
        if entry["status"] == "open" and not entry["note"] and not entry["rarity"]:
            items.pop(iid, None)
        else:
            items[iid] = entry
        _review_save(page, items)
        n = len(items)
    return jsonify({"ok": True, "count": n})


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
