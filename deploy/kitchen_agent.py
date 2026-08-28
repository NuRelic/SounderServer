#!/usr/bin/env python3
"""
Kitchen agent — plays the new server's sounds on the kitchen speakers.

Logs into the server, polls /api/active, and plays each active sound through a
pygame mixer (channels → safe overlap; never spawns parallel `aplay`, which
deadlocks the Pi's USB DAC). Audio is fetched from the server and cached locally.

THE POLL LOOP MUST NEVER BLOCK. Discovery is poll-only: a sound the loop doesn't
see during its short server-side lifetime is never played at all. So every slow
thing (audio downloads) happens on a background thread and the loop only ever
checks "is this file on disk yet?". See _download_worker / ensure_cached.

The kitchen plays INSTANTLY (it's the live output); synced browsers lag by the
sync buffer, so the room leads. Reversible: this just replaces soundboard.service
as the thing that owns the audio device.

Runs on any cheap Linux box with an audio out — see deploy/setup_node.sh, which
provisions a fresh Raspberry Pi OS Lite install into a working node.

Env:
  SS_NAME         room name shown in the server's online list (default "kitchen").
                  A SECOND node must set this or it collides with the kitchen.
  SS_SERVER       base URL (default https://sounderserver.party)
  SS_ORIGIN_IP    pin the server's IP, bypassing Cloudflare — see _PinnedHTTPSConnection
  SS_AUDIODEV     ALSA device (default hw:3,0). Prefer hw:CARD=<name> — card
                  NUMBERS move between reboots and a wrong one crash-loops the agent.
  SS_DRIVER       SDL audio driver (default alsa). Set "dummy" to test with no audio.
  SS_CACHE_DIR    audio cache location (default ~/kitchen_cache)
  SS_CACHE_CAP_MB cache size cap in MB (default 2048)
  SS_DL_DEADLINE  total seconds allowed for one download (default 180)
  SS_BLIND_WARN   log a warning when polling stalls this long (default 2.0s)
  SS_SONG_GAIN / SS_SONG_DUCK / SS_SOUND_GAIN   per-node mix levels, 0..1
  SS_PASSWORD     optional; NOT needed to listen. Leave unset on a box you don't own.
"""
import os, time, json, hashlib, shutil, socket, threading, queue, http.client, ssl
import urllib.request, urllib.parse, urllib.error, http.cookiejar

SERVER   = os.environ.get("SS_SERVER", "https://sounderserver.party")
PASSWORD = os.environ.get("SS_PASSWORD", "")   # set via env / the systemd unit
# Which room this box is. Shows up in the server's "who's online" list, so a
# second node in another house MUST set this or it collides with the kitchen.
NODE     = (os.environ.get("SS_NAME", "kitchen").strip() or "kitchen")[:40]
CACHE    = os.path.expanduser(os.environ.get("SS_CACHE_DIR", "~/kitchen_cache"))
# Cache cap. Every eviction means a later re-download, and on a small/slow card
# you want this well under the free space. Tunable for cheaper nodes.
CACHE_CAP = int(os.environ.get("SS_CACHE_CAP_MB", "2048")) * 1024**2
POLL     = 0.35
# Cloudflare intermittently tar-pits this agent's ~3 req/s of constant polling
# (fast TTFB, total time randomly ballooning to seconds) which turns into blind
# windows where fired sounds are never seen. Pinning the origin IP skips CF
# entirely while keeping SNI + cert validation for the real hostname, so it needs
# no /etc/hosts entry — cloud-init rewrites /etc/hosts on EVERY boot, which is
# exactly how the previous /etc/hosts-based bypass silently disappeared.
ORIGIN_IP = os.environ.get("SS_ORIGIN_IP", "").strip()
# Songs (loudness-normalized music) overpower the short clips (often quiet) on the
# box. Short sounds already play at max (Sound.set_volume can't amplify past 1.0),
# so we tame the song instead: a baseline reduction, and DUCK it while any short
# sound is firing so the sound cuts through. Both env-tunable (0..1).
SONG_GAIN = float(os.environ.get("SS_SONG_GAIN", "0.7"))   # song level when no sound is firing
SONG_DUCK = float(os.environ.get("SS_SONG_DUCK", "0.6"))   # song level while a sound is firing — only a slight dip so song + clip play at comparable volume
SOUND_GAIN = float(os.environ.get("SS_SOUND_GAIN", "0.6"))  # short-clip level relative to box volume — pulled below the song baseline so clips don't sit above songs
# A download gets a TOTAL deadline, not just a socket timeout. urllib's `timeout`
# is per-recv, so a connection that trickles bytes forever never trips it — that
# could wedge a download thread indefinitely.
DL_DEADLINE = float(os.environ.get("SS_DL_DEADLINE", "180"))
DL_SOCK_TIMEOUT = 15             # per-read timeout inside a download
DL_RETRY_COOLDOWN = 30.0         # don't hammer a file that just failed
BLIND_WARN = float(os.environ.get("SS_BLIND_WARN", "2.0"))  # log any gap this long between good polls
os.makedirs(CACHE, exist_ok=True)

os.environ.setdefault("SDL_AUDIODRIVER", os.environ.get("SS_DRIVER", "alsa"))
if os.environ["SDL_AUDIODRIVER"] == "alsa":
    os.environ.setdefault("AUDIODEV", os.environ.get("SS_AUDIODEV", "hw:3,0"))

import pygame
pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
pygame.mixer.set_num_channels(32)

# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------
class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TCP-connects to ORIGIN_IP but completes TLS for the real hostname.

    Same trick as `curl --resolve`: SNI and cert verification still use
    self.host, so this is a DNS override, NOT a security downgrade.
    """
    def connect(self):
        self.sock = socket.create_connection((ORIGIN_IP, self.port or 443), self.timeout)
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)

class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req)

_cj = http.cookiejar.CookieJar()

def _build_opener(pinned):
    handlers = [urllib.request.HTTPCookieProcessor(_cj)]
    if pinned and ORIGIN_IP:
        handlers.append(_PinnedHTTPSHandler())
    o = urllib.request.build_opener(*handlers)
    o.addheaders = [("User-Agent", "Mozilla/5.0 (X11; Linux aarch64) KitchenAgent/1.0")]
    return o

_opener     = _build_opener(pinned=True)    # polls (main thread)
_opener_dl  = _build_opener(pinned=True)    # downloads (worker thread)
_opener_dns = _build_opener(pinned=False)   # fallback if the pinned IP goes bad
# If the pin stops working (VPS re-IP'd, origin firewalled) fall back to normal
# DNS rather than going permanently silent.
_pin_fails = 0
_pin_until = 0.0                 # while now < this, use plain DNS
PIN_FAIL_LIMIT = 5               # ~15s of solid failure before falling back
PIN_COOLDOWN = 120.0             # a false fallback just means 2min on the slower CF path
# Both the poll loop and the download worker report into _pin_fails/_pin_until now,
# so the read-modify-write needs its own lock.
_PIN_LOCK = threading.Lock()

def _poll_opener():
    if ORIGIN_IP and time.time() < _pin_until:
        return _opener_dns
    return _opener

def _dl_opener():
    """Same pin/DNS choice as _poll_opener, for the download worker.

    Downloads used to be pinned UNCONDITIONALLY. On a node where new pinned
    connections stopped completing their TCP handshake, the poll loop limped along on
    its already-established keep-alive socket — so the agent looked healthy — while
    every single download failed forever and nothing uncached could ever play.
    """
    if ORIGIN_IP and time.time() < _pin_until:
        return _opener_dns
    return _opener_dl

def _note_pin_failure(who):
    """Count one transport failure against the origin pin; past the limit, trip the
    shared DNS cooldown. Lives here (not inline in the poll loop) because the download
    worker sees pin breakage the poll loop can miss, and vice versa."""
    global _pin_fails, _pin_until
    if not ORIGIN_IP:
        return
    with _PIN_LOCK:
        if time.time() < _pin_until:
            return               # already on DNS — this failure isn't the pin's doing
        _pin_fails += 1
        if _pin_fails < PIN_FAIL_LIMIT:
            return
        _pin_until = time.time() + PIN_COOLDOWN
        _pin_fails = 0
    # Drop the pinned socket so the next poll redials via DNS. Safe to do from the
    # download thread: worst case the poll loop is mid-request and sees a dead socket,
    # which it already handles by reconnecting — which is exactly what we want here.
    _poll_conn.close()
    print("origin pin failing (%s) — falling back to DNS for %.0fs" % (who, PIN_COOLDOWN))

def _note_pin_ok():
    global _pin_fails
    with _PIN_LOCK:
        _pin_fails = 0

def login():
    data = json.dumps({"password": PASSWORD}).encode()
    req = urllib.request.Request(SERVER + "/api/login", data=data,
                                 headers={"Content-Type": "application/json"})
    return json.loads(_poll_opener().open(req, timeout=15).read())

class _PollConn:
    """One reused keep-alive connection for /api/active.

    urllib opens a fresh TCP+TLS connection per request, i.e. ~3 handshakes/second
    forever. That's wasteful at rest and actively harmful while an audio download
    is saturating the link: a handshake-per-poll pushed poll latency from ~0.08s to
    ~0.7s, and the loop's blind gaps to 2.15s — long enough to miss a short clip
    (they only live ~2.6s server-side). Reusing the socket keeps polls fast.
    """
    def __init__(self):
        u = urllib.parse.urlparse(SERVER)
        self._host = u.hostname
        self._port = u.port or 443
        self._c = None

    def _connect(self, timeout):
        pinned = ORIGIN_IP and time.time() >= _pin_until
        cls = _PinnedHTTPSConnection if pinned else http.client.HTTPSConnection
        return cls(self._host, self._port, timeout=timeout)

    def close(self):
        if self._c is not None:
            try: self._c.close()
            except Exception: pass
            self._c = None

    def get_json(self, path, timeout=3):
        # Short timeout on purpose: we poll every POLL seconds, so a poll that hasn't
        # returned in a few seconds is already stale — better to abandon it and let the
        # NEXT poll catch the current state than to block the loop. A long timeout here
        # turns a single slow response into a multi-second window where the kitchen is
        # blind and any short sound fired in that window is never seen (so never plays).
        t0 = time.monotonic()
        for attempt in (0, 1):
            if self._c is None:
                self._c = self._connect(timeout)
            try:
                self._c.request("GET", path, headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) KitchenAgent/1.0",
                    "Connection": "keep-alive", "Accept": "application/json"})
                r = self._c.getresponse()
                body = r.read()          # must drain, or the socket can't be reused
                if r.status != 200:
                    raise urllib.error.HTTPError(SERVER + path, r.status, r.reason, r.headers, None)
                return json.loads(body)
            except urllib.error.HTTPError:
                raise                    # a real server answer — let the loop handle the code
            except Exception:
                self.close()
                # Only retry a dead keep-alive socket, and only if we haven't already
                # burned the time budget — retrying after a timeout would double the
                # blind window instead of shortening it.
                if attempt or time.monotonic() - t0 > 1.0:
                    raise

_poll_conn = _PollConn()

def get_active():
    return _poll_conn.get_json("/api/active?u=" + urllib.parse.quote(NODE))

# ---------------------------------------------------------------------------
# Cache + background downloads
# ---------------------------------------------------------------------------
def _evict_cache(keep):
    """Keep the cache under CACHE_CAP, deleting least-recently-used files first.
    Never deletes a path in `keep` (the song currently streamed by mixer.music,
    which reads from disk on demand, plus whatever we just downloaded). Short
    sounds are fully decoded into RAM at load, so their cache files are safe to
    drop even mid-play."""
    try:
        files, total = [], 0
        for n in os.listdir(CACHE):
            p = os.path.join(CACHE, n)
            try: st = os.stat(p)
            except OSError: continue
            if not os.path.isfile(p): continue
            total += st.st_size
            files.append((st.st_atime, st.st_size, p))
        if total <= CACHE_CAP:
            return
        files.sort()                         # oldest access first
        for _at, size, p in files:
            if total <= CACHE_CAP:
                break
            if p in keep:
                continue
            try: os.remove(p); total -= size
            except OSError: pass
    except Exception:
        pass

def _sweep_zero_byte():
    """Clear out 0-byte cache files once at startup.

    Every one of these is a sound that can never play: they're leftovers from before
    _download validated its output, and the old existence-only cache check treated them
    as permanently cached. One node had accumulated 59 of them. Cheap to redo each boot,
    and it means an upgraded node heals itself instead of needing a manual purge."""
    n = 0
    try:
        for name in os.listdir(CACHE):
            p = os.path.join(CACHE, name)
            try:
                if os.path.isfile(p) and os.path.getsize(p) == 0:
                    os.remove(p); n += 1
            except OSError:
                pass
    except OSError:
        return
    if n:
        print("cache: removed %d zero-byte file%s (they will re-download on demand)"
              % (n, "" if n == 1 else "s"))

def cache_path(fn, ver=0):
    ext = os.path.splitext(fn)[1] or ".wav"
    # ver (the file's mtime) is part of the cache key, so an edit on the server
    # (trim / volume change) produces a new key and we re-download the new audio.
    return os.path.join(CACHE, hashlib.md5(("%s@%s" % (fn, ver)).encode()).hexdigest() + ext)

_DL_Q = queue.Queue()
_DL_LOCK = threading.Lock()
_DL_INFLIGHT = set()             # cache paths being downloaded right now
_DL_FAILED = {}                  # cache path -> ts of last failure

def ensure_cached(fn, ver=0):
    """Return the local path if the audio is already on disk, else kick off a
    background download and return None. NEVER blocks — that's the whole point."""
    path = cache_path(fn, ver)
    # Size, not existence: a 0-byte cache file (interrupted download, disk full) used to
    # satisfy os.path.exists forever, so the sound counted as cached and was never
    # re-fetched — it just silently failed to play, permanently. Delete the corpse so
    # the normal download path below picks it up.
    try:
        if os.path.getsize(path) > 0:
            return path
        os.remove(path)
    except OSError:
        pass                     # not cached yet, or another thread already unlinked it
    with _DL_LOCK:
        if path in _DL_INFLIGHT:
            return None
        last = _DL_FAILED.get(path)
        if last and time.time() - last < DL_RETRY_COOLDOWN:
            return None
        _DL_INFLIGHT.add(path)
    _DL_Q.put((fn, ver, path))
    return None

def _download(fn, ver, path):
    url = SERVER + "/api/audio?f=" + urllib.parse.quote(fn) + (("&v=%s" % ver) if ver else "")
    tmp = "%s.%d.tmp" % (path, os.getpid())
    t0 = time.monotonic()
    try:
        # stream straight to disk — a 1 GB mix must not buffer in RAM. Modest 256 KB
        # chunks (not 1 MB) so this thread yields often enough that the poll loop
        # stays snappy while a big song downloads.
        got = 0
        want = -1                # -1 = server sent no usable Content-Length
        with _dl_opener().open(url, timeout=DL_SOCK_TIMEOUT) as r, open(tmp, "wb") as f:
            try: want = int(r.headers.get("Content-Length"))
            except (TypeError, ValueError): pass
            while True:
                if time.monotonic() - t0 > DL_DEADLINE:
                    raise TimeoutError("exceeded %.0fs total deadline" % DL_DEADLINE)
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        # Validate BEFORE promoting. os.replace is what turned an empty or truncated
        # response into a "cached" file that outlived every retry. Raising instead lets
        # the finally block bin the temp file and the worker record a normal failure,
        # so the next play attempt tries again after the cooldown. (r/f are closed by
        # now — os.replace already sat outside the `with`.)
        if got == 0:
            raise IOError("empty response")
        if want >= 0 and got != want:
            raise IOError("truncated: got %d of %d bytes" % (got, want))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
    _evict_cache({path} | ({_song_path} if _song_path else set()))
    print("cached %s (%.1f MB in %.1fs)"
          % (fn, os.path.getsize(path) / 1048576.0, time.monotonic() - t0))

# ---------------------------------------------------------------------------
# Self-reporting — the node had NO way to tell the server anything about itself,
# so an "online but silent" node (failed downloads, wrong audio device, 0-byte
# cache) was indistinguishable from a healthy one without SSHing in. This posts a
# small health blob every REPORT_EVERY seconds on its own thread (never the poll
# loop) so the server can surface it. Best-effort: any failure is swallowed.
NODE_VERSION = "2026.08.28"
REPORT_EVERY = 45
_START = time.monotonic()
_LAST_DL_OK = 0.0                # wall-clock ts of the last successful download
_DL_OK = 0                       # lifetime successful downloads
_DL_FAIL = 0                     # lifetime failed downloads
_BLIND = 0                       # lifetime blind-window warnings
_REPORT_LOCK = threading.Lock()

def _cache_stats():
    n = z = 0; total = 0
    try:
        with os.scandir(CACHE) as it:
            for e in it:
                if not e.is_file(): continue
                try: sz = e.stat().st_size
                except OSError: continue
                n += 1; total += sz
                if sz == 0: z += 1
    except OSError:
        pass
    return n, z, total

def _report_once():
    n, zero, total = _cache_stats()
    body = json.dumps({
        "name": NODE, "version": NODE_VERSION,
        "uptime_s": int(time.monotonic() - _START),
        "audiodev": os.environ.get("AUDIODEV", os.environ.get("SDL_AUDIODRIVER", "?")),
        "cache_mb": round(total / 1048576.0, 1), "cache_files": n, "cache_zero": zero,
        "cache_cap_mb": CACHE_CAP // 1048576,
        "last_dl_age_s": int(time.time() - _LAST_DL_OK) if _LAST_DL_OK else -1,
        "dl_ok": _DL_OK, "dl_fail": _DL_FAIL, "blind": _BLIND,
        "on_dns_fallback": bool(ORIGIN_IP and time.time() < _pin_until),
    }).encode()
    req = urllib.request.Request(SERVER + "/api/node/report", data=body,
                                 headers={"Content-Type": "application/json"})
    _poll_opener().open(req, timeout=10).read()

def _report_loop():
    while True:
        try: _report_once()
        except Exception: pass          # health reporting must never disturb playback
        time.sleep(REPORT_EVERY)

def _download_worker():
    global _LAST_DL_OK, _DL_OK, _DL_FAIL
    while True:
        fn, ver, path = _DL_Q.get()
        try:
            _download(fn, ver, path)
            with _DL_LOCK:
                _DL_FAILED.pop(path, None)
            with _REPORT_LOCK: _LAST_DL_OK = time.time(); _DL_OK += 1
        except Exception as e:
            with _DL_LOCK:
                _DL_FAILED[path] = time.time()
            with _REPORT_LOCK: _DL_FAIL += 1
            print("download error:", fn, e)
            # An HTTPError is a real answer from the server, so the transport is fine.
            # Anything else (connect refused, TLS timeout, truncation) is evidence the
            # pinned path is broken and should count toward the DNS fallback.
            if not isinstance(e, urllib.error.HTTPError):
                _note_pin_failure("download")
        finally:
            with _DL_LOCK:
                _DL_INFLIGHT.discard(path)
            _DL_Q.task_done()

def _is_song(entry):
    return str(entry.get("lane", "")).startswith("song")

# --- short sounds: loaded as Sounds and mixed on channels (real overlap) ---
_playing = {}   # token -> (channel, Sound)

def play_short(entry, vol):
    tok = entry["token"]
    if tok in _playing:
        return
    path = ensure_cached(entry["file"], entry.get("ver", 0))
    if path is None:
        return          # downloading; a later poll plays it if it's still active
    try:
        snd = pygame.mixer.Sound(path)
        snd.set_volume(max(0.0, min(1.0, vol)))
        ch = pygame.mixer.find_channel(True)   # steal oldest if all busy
        ch.play(snd)
        _playing[tok] = (ch, snd)
        print("▶", entry.get("name"), "lane", entry.get("lane"), "by", entry.get("by"))
    except Exception as e:
        print("play error:", entry.get("file"), e)

# --- songs: STREAMED via mixer.music so a 5-min (or 45-min) file uses almost no
# RAM. Loading them as Sounds decoded the whole song into memory (~130MB for 5
# min, ~1GB for the long mixes), which thrashed the Pi and went silent. music is
# a single stream, so with >1 song lane only the most-recent song is audible. ---
_song_tok = None
_song_path = None      # disk path of the streaming song — protected from cache eviction

def play_song(entry, vol):
    global _song_tok, _song_path
    v = max(0.0, min(1.0, vol))
    if entry["token"] == _song_tok:
        try: pygame.mixer.music.set_volume(v)
        except Exception: pass
        return
    path = ensure_cached(entry["file"], entry.get("ver", 0))
    if path is None:
        return          # downloading; polling continues so clips still fire
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(v)
        # If we spent time downloading, join the song where it actually is rather
        # than restarting it — keeps the room roughly in step with synced browsers.
        behind = max(0.0, time.time() - float(entry.get("start") or 0))
        if behind > 2.0 and entry.get("start"):
            try:
                pygame.mixer.music.play(start=behind)
            except Exception:
                pygame.mixer.music.play()      # SDL can't seek this format
        else:
            pygame.mixer.music.play()
        _song_tok = entry["token"]; _song_path = path
        print("▶ song", entry.get("name"), "by", entry.get("by"),
              ("(joined %.0fs in)" % behind) if behind > 2.0 else "")
    except Exception as e:
        print("song play error:", entry.get("file"), e)

def stop_song():
    global _song_tok, _song_path
    if _song_tok is not None:
        try: pygame.mixer.music.stop()
        except Exception: pass
        _song_tok = None; _song_path = None

def try_login():
    """Best-effort login. Listening no longer requires auth, so a failure here
    must NOT block the play loop (the kitchen only reads the public /api/active).
    A password is only useful if a future build re-gates the public endpoints."""
    if not PASSWORD:
        return False
    try:
        print("  ->", login()); return True
    except Exception as e:
        print("login skipped (%s) — listening is open, continuing" % e); return False

def run():
    global _BLIND
    print("node %r -> %s %s | audio %s | cache %s cap %dMB"
          % (NODE, SERVER, ("(origin-pinned %s)" % ORIGIN_IP) if ORIGIN_IP else "(via DNS)",
             os.environ.get("AUDIODEV", os.environ["SDL_AUDIODRIVER"]),
             CACHE, CACHE_CAP // 1024**2))
    _sweep_zero_byte()           # before the worker starts, so nothing races the unlink
    threading.Thread(target=_download_worker, daemon=True, name="dl").start()
    threading.Thread(target=_report_loop, daemon=True, name="report").start()
    try_login()
    print("kitchen agent running. Ctrl-C to stop.")
    last_ok = time.monotonic()
    while True:
        try:
            d = get_active()
            # Watchdog: discovery is poll-only, so a gap here is a window where
            # fired sounds were invisible. Short clips live ~2.6s server-side, so
            # anything over that silently dropped a sound — log it, don't guess later.
            gap = time.monotonic() - last_ok
            if gap >= BLIND_WARN:
                print("⚠ blind for %.1fs (sounds fired in that window were missed)" % gap)
                with _REPORT_LOCK: _BLIND += 1
            last_ok = time.monotonic()
            _note_pin_ok()
            active = d.get("active", [])
            vol = d.get("box_volume", 100) / 100.0
            live = {a["token"] for a in active}
            shorts = [a for a in active if not _is_song(a)]
            songs  = [a for a in active if _is_song(a)]
            # short sounds — overlap on channels, pulled a touch below box volume
            # so clips don't sit so far above the songs.
            sound_vol = vol * SOUND_GAIN
            for a in shorts:
                if a["token"] not in _playing:
                    play_short(a, sound_vol)
                else:
                    # update the SOUND's volume (index 1), NOT the channel (index 0):
                    # play_short sets snd volume and leaves the channel at 1.0, so
                    # setting the channel here too would compound to vol*vol and the
                    # clip would drop after the first poll.
                    try: _playing[a["token"]][1].set_volume(sound_vol)
                    except Exception: pass
            for tok in list(_playing):
                if tok not in live:                 # interrupted/finished on the server
                    ch, _ = _playing.pop(tok)
                    try: ch.stop()
                    except Exception: pass
            # song — single streamed lane (most-recent wins if >1 song is active).
            # Duck it while any short sound is playing so the sound cuts through.
            cur = songs[-1] if songs else None
            if cur:
                song_vol = vol * (SONG_DUCK if shorts else SONG_GAIN)
                play_song(cur, song_vol)
            if _song_tok is not None and _song_tok not in live:
                stop_song()
        except urllib.error.HTTPError as e:
            # 401/403 = our session was dropped (e.g. a server bounce) -> re-login so
            # the kitchen self-heals without anyone restarting the Pi. Other codes
            # (502 while the backend reboots) just retry.
            if e.code in (401, 403):                # only if a future build re-gates /api/active
                print("auth lost (%s) — re-logging in" % e.code); try_login()
            else:
                print("loop error:", e)             # fall through to the normal POLL sleep and retry
        except Exception as e:
            # Don't add an extra sleep here: a failed/slow poll should NOT extend the
            # blind window. The trailing time.sleep(POLL) already paces retries, so we
            # resume polling within POLL seconds and catch sounds we'd otherwise miss.
            print("loop error:", e)
            _note_pin_failure("poll")
        time.sleep(POLL)

if __name__ == "__main__":
    run()
