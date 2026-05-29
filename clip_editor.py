"""
In-browser clip editor backend for the soundboard.

Lets the web UI:
  * fetch a source (YouTube URL or uploaded file) into a STAGING area,
  * or open an EXISTING library sound (song / sfx / dcc) for editing,
  * preview/trim a segment in the browser (served as preview.mp3),
  * save the trimmed segment as a NEW song or sfx (normalizer handles songs),
  * or OVERWRITE an existing sound in place.

Self-contained: registers its own /api/clip/* routes via init(webapp, ctx) and
never touches the pygame/audio device. Any failure is contained to these routes.

ctx (dict) must provide:
  slot1_folder, sfx_dir, slot8_folder, staging_dir   (paths)
  groups()            -> current SLOT1_GROUPS list of (name, start, end)
  create_new_group(name) -> (name, start, end)
  load_slot1_sounds() -> reload song groups after a song change
  get_file_number(fn) -> int
  get_display_name(fn)-> str
  logger
"""

import os
import re
import shutil
import subprocess
import threading
import time
import uuid

from flask import request, jsonify, send_file, abort, session

_SESSIONS = {}
_LOCK = threading.Lock()
_SESSION_TTL = 3600  # seconds; stale staging dirs are reaped


def _sanitize(name):
    name = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    return "".join(c for c in name if c.isalnum() or c == "_")


def _duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return round(float(r.stdout.strip() or 0), 3)
    except Exception:
        return 0.0


def init(webapp, ctx):
    log = ctx["logger"]
    STAGING = ctx["staging_dir"]
    os.makedirs(STAGING, exist_ok=True)

    def _sess_dir(sid):
        return os.path.join(STAGING, sid)

    def _reap_stale():
        now = time.time()
        with _LOCK:
            dead = [sid for sid, s in _SESSIONS.items()
                    if now - s.get("created", now) > _SESSION_TTL]
            for sid in dead:
                _SESSIONS.pop(sid, None)
                shutil.rmtree(_sess_dir(sid), ignore_errors=True)

    def _make_preview(src, sid):
        """Create a small mp3 the browser can decode for waveform + playback."""
        preview = os.path.join(_sess_dir(sid), "preview.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
             "-ac", "2", "-ar", "44100", "-b:a", "96k", preview],
            capture_output=True, text=True, timeout=180,
        )
        return preview

    # ---- background source acquisition -------------------------------------
    def _download_youtube(sid, url):
        d = _sess_dir(sid)
        try:
            r = subprocess.run(
                ["yt-dlp", "--no-playlist", "-x", "--audio-format", "wav",
                 "-o", os.path.join(d, "source.%(ext)s"), url],
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode != 0:
                _fail(sid, "Download failed: " + (r.stderr or "")[-200:])
                return
            src = os.path.join(d, "source.wav")
            if not os.path.exists(src):
                _fail(sid, "Download produced no audio")
                return
            _make_preview(src, sid)
            _ready(sid, src)
        except subprocess.TimeoutExpired:
            _fail(sid, "Download timed out")
        except Exception as e:
            _fail(sid, "Download error: %r" % e)

    def _ingest_upload(sid, tmp_path):
        d = _sess_dir(sid)
        try:
            src = os.path.join(d, "source.wav")
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp_path,
                 "-ac", "2", "-ar", "44100", src],
                capture_output=True, text=True, timeout=300,
            )
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            if r.returncode != 0 or not os.path.exists(src):
                _fail(sid, "Could not decode uploaded file")
                return
            _make_preview(src, sid)
            _ready(sid, src)
        except Exception as e:
            _fail(sid, "Upload error: %r" % e)

    def _ready(sid, src):
        with _LOCK:
            s = _SESSIONS.get(sid)
            if s is not None:
                s["status"] = "ready"
                s["src"] = src
                s["duration"] = _duration(src)

    def _fail(sid, msg):
        log.warning("[clip] %s" % msg)
        with _LOCK:
            s = _SESSIONS.get(sid)
            if s is not None:
                s["status"] = "error"
                s["error"] = msg

    def _new_session(origin, **extra):
        sid = uuid.uuid4().hex[:16]
        os.makedirs(_sess_dir(sid), exist_ok=True)
        with _LOCK:
            _SESSIONS[sid] = {
                "status": "working", "origin": origin, "error": "",
                "duration": 0.0, "src": None, "created": time.time(),
                "stack": [], **extra,
            }
        return sid

    def _get(sid):
        with _LOCK:
            return _SESSIONS.get(sid)

    # ---- numbering for new songs (mirrors api_add / api_upload_song) --------
    def _next_song_number(group, new_group_name):
        slot1 = ctx["slot1_folder"]
        nums = set()
        if os.path.isdir(slot1):
            for f in os.listdir(slot1):
                if f.startswith("sound_") and f.endswith(".wav"):
                    n = ctx["get_file_number"](f)
                    if n > 0:
                        nums.add(n)
        if new_group_name:
            info = ctx["create_new_group"](new_group_name)
            return info[1], new_group_name
        if group:
            for gname, start, end in ctx["groups"]():
                if gname == group:
                    gn = [n for n in nums if start <= n <= end]
                    return (max(gn) + 1 if gn else start), group
        return (max(nums) + 1 if nums else 1), (group or "")

    def _trim(src, start, end, out, gain=1.0):
        dur = max(0.05, float(end) - float(start))
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-i", src, "-ss", "%.3f" % float(start), "-t", "%.3f" % dur]
        try:
            g = float(gain)
        except (TypeError, ValueError):
            g = 1.0
        if abs(g - 1.0) > 0.01:                      # apply volume filter only if changed
            cmd += ["-af", "volume=%.3f" % max(0.0, min(4.0, g))]
        cmd += ["-ac", "2", "-ar", "44100", out]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return r.returncode == 0 and os.path.exists(out)

    # ====================================================================
    # ROUTES
    # ====================================================================
    @webapp.route("/api/clip/fetch", methods=["POST"])
    def clip_fetch():
        _reap_stale()
        # multipart upload?
        if "file" in request.files:
            f = request.files["file"]
            if not f or not f.filename:
                return jsonify({"error": "No file"}), 400
            sid = _new_session("upload")
            tmp = os.path.join(_sess_dir(sid), "upload.bin")
            f.save(tmp)
            threading.Thread(target=_ingest_upload, args=(sid, tmp), daemon=True).start()
            return jsonify({"id": sid, "status": "working"})
        # else JSON url
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"error": "Need a URL or file"}), 400
        sid = _new_session("youtube", url=url)
        threading.Thread(target=_download_youtube, args=(sid, url), daemon=True).start()
        return jsonify({"id": sid, "status": "working"})

    @webapp.route("/api/clip/open", methods=["POST"])
    def clip_open():
        """Open an existing library sound for editing."""
        _reap_stale()
        data = request.get_json(silent=True) or {}
        kind = data.get("kind")          # song | sfx | dcc
        category = data.get("category", "")
        filename = data.get("filename", "")
        if not filename.endswith(".wav") or "/" in filename or ".." in filename:
            return jsonify({"error": "Invalid filename"}), 400
        category = "" if (".." in category or "/" in category) else category
        if kind == "song":
            orig = os.path.join(ctx["slot1_folder"], filename)
        elif kind == "sfx":
            orig = os.path.join(ctx["sfx_dir"], category, filename)
        elif kind == "dcc":
            orig = os.path.join(ctx["slot8_folder"], category, filename)
        else:
            return jsonify({"error": "Invalid kind"}), 400
        if not os.path.isfile(orig):
            return jsonify({"error": "File not found"}), 404
        sid = _new_session("existing", orig_path=orig, orig_kind=kind)
        src = os.path.join(_sess_dir(sid), "source.wav")
        try:
            shutil.copy2(orig, src)
            _make_preview(src, sid)
            _ready(sid, src)
        except Exception as e:
            _fail(sid, "Open failed: %r" % e)
            return jsonify({"error": "Could not open file"}), 500
        return jsonify({"id": sid, "status": "ready",
                        "duration": _duration(src), "name": filename})

    @webapp.route("/api/clip/status/<sid>")
    def clip_status(sid):
        s = _get(sid)
        if not s:
            return jsonify({"error": "Unknown session"}), 404
        return jsonify({"status": s["status"], "error": s["error"],
                        "duration": s["duration"], "origin": s["origin"]})

    @webapp.route("/api/clip/audio/<sid>")
    def clip_audio(sid):
        s = _get(sid)
        if not s:
            abort(404)
        preview = os.path.join(_sess_dir(sid), "preview.mp3")
        if not os.path.exists(preview):
            abort(404)
        return send_file(preview, mimetype="audio/mpeg", conditional=True)

    @webapp.route("/api/clip/save", methods=["POST"])
    def clip_save():
        data = request.get_json(silent=True) or {}
        sid = data.get("id")
        s = _get(sid)
        if not s or s["status"] != "ready" or not s.get("src"):
            return jsonify({"error": "Source not ready"}), 400
        try:
            start = float(data.get("start", 0))
            end = float(data.get("end", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "Bad start/end"}), 400
        if end <= start:
            return jsonify({"error": "End must be after start"}), 400
        name = _sanitize(data.get("name", ""))
        if not name:
            return jsonify({"error": "Need a name"}), 400
        dest = data.get("dest", "song")
        gain = data.get("gain", 1.0)

        if dest == "sfx":
            category = data.get("category", "")
            if not category or "/" in category or ".." in category:
                return jsonify({"error": "Need an SFX category"}), 400
            catdir = os.path.join(ctx["sfx_dir"], category)
            os.makedirs(catdir, exist_ok=True)
            out = os.path.join(catdir, name + ".wav")
            if not _trim(s["src"], start, end, out, gain):
                return jsonify({"error": "Trim failed"}), 500
            return jsonify({"status": "saved", "dest": "sfx",
                            "filename": name + ".wav", "category": category})

        # default: song
        group = data.get("group", "")
        new_group = data.get("new_group", "")
        number, group = _next_song_number(group, new_group)
        filename = "sound_%05d_%s.wav" % (int(number), name)
        out = os.path.join(ctx["slot1_folder"], filename)
        if not _trim(s["src"], start, end, out, gain):
            return jsonify({"error": "Trim failed"}), 500
        try:
            ctx["load_slot1_sounds"]()
        except Exception:
            pass
        return jsonify({"status": "saved", "dest": "song",
                        "filename": filename, "number": number, "group": group})

    @webapp.route("/api/clip/overwrite", methods=["POST"])
    def clip_overwrite():
        data = request.get_json(silent=True) or {}
        sid = data.get("id")
        s = _get(sid)
        if not s or s["status"] != "ready" or not s.get("src"):
            return jsonify({"error": "Source not ready"}), 400
        if s["origin"] != "existing" or not s.get("orig_path"):
            return jsonify({"error": "Not an existing sound"}), 400
        try:
            start = float(data.get("start", 0))
            end = float(data.get("end", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "Bad start/end"}), 400
        if end <= start:
            return jsonify({"error": "End must be after start"}), 400
        orig = s["orig_path"]
        tmp = orig + ".edit.tmp.wav"
        if not _trim(s["src"], start, end, tmp, data.get("gain", 1.0)):
            return jsonify({"error": "Trim failed"}), 500
        try:
            os.replace(tmp, orig)   # mtime bump -> normalizer re-processes songs
            # clear stale .normalized marker so songs definitely re-normalize
            marker = orig + ".normalized"
            if os.path.exists(marker):
                try:
                    os.remove(marker)
                except Exception:
                    pass
            if s["orig_kind"] == "song":
                ctx["load_slot1_sounds"]()
        except Exception as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            return jsonify({"error": "Overwrite failed: %r" % e}), 500
        return jsonify({"status": "overwritten",
                        "filename": os.path.basename(orig)})

    @webapp.route("/api/clip/discard/<sid>", methods=["POST"])
    def clip_discard(sid):
        with _LOCK:
            _SESSIONS.pop(sid, None)
        shutil.rmtree(_sess_dir(sid), ignore_errors=True)
        return jsonify({"status": "discarded"})

    @webapp.route("/api/clip/list")
    def clip_list():
        """List existing sounds for the 'edit existing' picker."""
        kind = request.args.get("kind", "song")
        out = []
        try:
            if kind == "song":
                slot1 = ctx["slot1_folder"]
                for f in sorted(os.listdir(slot1)):
                    if f.endswith(".wav") and ".tmp." not in f and os.path.isfile(os.path.join(slot1, f)):
                        out.append({"filename": f, "category": "",
                                    "display": ctx["get_display_name"](f)})
            elif kind in ("sfx", "dcc"):
                base = ctx["sfx_dir"] if kind == "sfx" else ctx["slot8_folder"]
                if os.path.isdir(base):
                    for d in sorted(os.listdir(base)):
                        dp = os.path.join(base, d)
                        if os.path.isdir(dp):
                            for f in sorted(os.listdir(dp)):
                                if f.endswith(".wav"):
                                    out.append({"filename": f, "category": d,
                                                "display": d + " / " + f[:-4]})
        except Exception as e:
            log.warning("[clip] list error: %r" % e)
        return jsonify({"kind": kind, "items": out})

    @webapp.route("/api/clip/delete", methods=["POST"])
    def clip_delete():
        """Delete an existing library sound. ADMIN ONLY (checks the session)."""
        if not bool(session.get("admin")):
            return jsonify({"error": "Admin only"}), 403
        data = request.get_json(silent=True) or {}
        kind = data.get("kind")
        category = data.get("category", "")
        filename = data.get("filename", "")
        if not filename.endswith(".wav") or "/" in filename or ".." in filename:
            return jsonify({"error": "Invalid filename"}), 400
        category = "" if ("/" in category or ".." in category) else category
        if kind == "song":
            target = os.path.join(ctx["slot1_folder"], filename)
        elif kind == "sfx":
            target = os.path.join(ctx["sfx_dir"], category, filename)
        elif kind == "dcc":
            target = os.path.join(ctx["slot8_folder"], category, filename)
        else:
            return jsonify({"error": "Invalid kind"}), 400
        if not os.path.isfile(target):
            return jsonify({"error": "File not found"}), 404
        try:
            os.remove(target)
            for mk in (target + ".normalized", target + ".normalize_failed"):
                if os.path.exists(mk):
                    try:
                        os.remove(mk)
                    except Exception:
                        pass
            if kind == "song":
                ctx["load_slot1_sounds"]()
        except Exception as e:
            return jsonify({"error": "Delete failed: %r" % e}), 500
        log.info("[clip] deleted %s/%s" % (kind, filename))
        return jsonify({"status": "deleted", "filename": filename})

    @webapp.route("/api/clip/crop", methods=["POST"])
    def clip_crop():
        """Narrow the working audio to [start,end] for finer selection. Reversible."""
        data = request.get_json(silent=True) or {}
        sid = data.get("id")
        s = _get(sid)
        if not s or s["status"] != "ready" or not s.get("src"):
            return jsonify({"error": "Source not ready"}), 400
        try:
            start = float(data.get("start", 0))
            end = float(data.get("end", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "Bad start/end"}), 400
        if end - start < 0.1:
            return jsonify({"error": "Selection too short to crop"}), 400
        d = _sess_dir(sid)
        newsrc = os.path.join(d, "crop%d.wav" % (len(s.get("stack", [])) + 1))
        if not _trim(s["src"], start, end, newsrc):
            return jsonify({"error": "Crop failed"}), 500
        s.setdefault("stack", []).append(s["src"])
        s["src"] = newsrc
        try:
            _make_preview(newsrc, sid)
        except Exception:
            pass
        s["duration"] = _duration(newsrc)
        return jsonify({"status": "cropped", "duration": s["duration"],
                        "depth": len(s["stack"])})

    @webapp.route("/api/clip/uncrop", methods=["POST"])
    def clip_uncrop():
        """Revert the last crop."""
        data = request.get_json(silent=True) or {}
        sid = data.get("id")
        s = _get(sid)
        if not s:
            return jsonify({"error": "Unknown session"}), 404
        stack = s.get("stack", [])
        if not stack:
            return jsonify({"error": "Nothing to uncrop"}), 400
        prev = stack.pop()
        cur = s.get("src")
        # delete the cropped working file we're discarding
        try:
            if cur and cur != prev and os.path.basename(cur).startswith("crop"):
                os.remove(cur)
        except Exception:
            pass
        s["src"] = prev
        try:
            _make_preview(prev, sid)
        except Exception:
            pass
        s["duration"] = _duration(prev)
        return jsonify({"status": "uncropped", "duration": s["duration"],
                        "depth": len(stack)})

    log.info("\U0001F3B5 Clip editor enabled at /api/clip/*")
