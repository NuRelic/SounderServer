#!/usr/bin/env python3
"""
Kitchen agent — plays the new server's sounds on the kitchen speakers.

Logs into the server, polls /api/active, and plays each active sound through a
pygame mixer (channels → safe overlap; never spawns parallel `aplay`, which
deadlocks the Pi's USB DAC). Audio is fetched from the server and cached locally.

The kitchen plays INSTANTLY (it's the live output); synced browsers lag by the
sync buffer, so the room leads. Reversible: this just replaces soundboard.service
as the thing that owns the audio device.

Env: SS_SERVER, SS_PASSWORD, SS_AUDIODEV (default hw:3,0), SS_DRIVER (default alsa).
Set SS_DRIVER=dummy to test login/polling with no real audio output.
"""
import os, time, json, hashlib, shutil, urllib.request, urllib.parse, urllib.error, http.cookiejar

SERVER   = os.environ.get("SS_SERVER", "https://sounderserver.party")
PASSWORD = os.environ.get("SS_PASSWORD", "")   # set via env / the systemd unit
CACHE    = os.path.expanduser("~/kitchen_cache")
CACHE_CAP = 2 * 1024**3          # keep the SD-card cache under ~2 GB
POLL     = 0.35
# Songs (loudness-normalized music) overpower the short clips (often quiet) on the
# box. Short sounds already play at max (Sound.set_volume can't amplify past 1.0),
# so we tame the song instead: a baseline reduction, and DUCK it while any short
# sound is firing so the sound cuts through. Both env-tunable (0..1).
SONG_GAIN = float(os.environ.get("SS_SONG_GAIN", "0.7"))   # song level when no sound is firing
SONG_DUCK = float(os.environ.get("SS_SONG_DUCK", "0.3"))   # song level while a sound is firing
os.makedirs(CACHE, exist_ok=True)

os.environ.setdefault("SDL_AUDIODRIVER", os.environ.get("SS_DRIVER", "alsa"))
if os.environ["SDL_AUDIODRIVER"] == "alsa":
    os.environ.setdefault("AUDIODEV", os.environ.get("SS_AUDIODEV", "hw:3,0"))

import pygame
pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
pygame.mixer.set_num_channels(32)

_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))
_opener.addheaders = [("User-Agent", "Mozilla/5.0 (X11; Linux aarch64) KitchenAgent/1.0")]

def login():
    data = json.dumps({"password": PASSWORD}).encode()
    req = urllib.request.Request(SERVER + "/api/login", data=data,
                                 headers={"Content-Type": "application/json"})
    return json.loads(_opener.open(req, timeout=15).read())

def get_active():
    r = _opener.open(SERVER + "/api/active?u=kitchen", timeout=10)
    return json.loads(r.read())

def _evict_cache(keep):
    """Keep the cache under CACHE_CAP, deleting least-recently-used files first.
    Never deletes a path in `keep` (the song currently streamed by mixer.music,
    which reads from disk on demand). Short sounds are fully decoded into RAM at
    load, so their cache files are safe to drop even mid-play."""
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

def fetch(fn, ver=0):
    ext = os.path.splitext(fn)[1] or ".wav"
    # ver (the file's mtime) is part of the cache key, so an edit on the server
    # (trim / volume change) produces a new key and we re-download the new audio.
    key  = "%s@%s" % (fn, ver)
    path = os.path.join(CACHE, hashlib.md5(key.encode()).hexdigest() + ext)
    if not os.path.exists(path):
        url = SERVER + "/api/audio?f=" + urllib.parse.quote(fn) + (("&v=%s" % ver) if ver else "")
        tmp = path + ".tmp"
        # stream straight to disk in 1 MB chunks — a 1 GB mix must not buffer in RAM
        with _opener.open(url, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        os.replace(tmp, path)
        _evict_cache({_song_path} if _song_path else set())
    return path

def _is_song(entry):
    return str(entry.get("lane", "")).startswith("song")

# --- short sounds: loaded as Sounds and mixed on channels (real overlap) ---
_playing = {}   # token -> (channel, Sound)

def play_short(entry, vol):
    tok = entry["token"]
    if tok in _playing:
        return
    try:
        snd = pygame.mixer.Sound(fetch(entry["file"], entry.get("ver", 0)))
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
    try:
        p = fetch(entry["file"], entry.get("ver", 0))
        pygame.mixer.music.load(p)
        pygame.mixer.music.set_volume(v)
        pygame.mixer.music.play()
        _song_tok = entry["token"]; _song_path = p
        print("▶ song", entry.get("name"), "by", entry.get("by"))
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
    print("connecting to", SERVER, "...")
    try_login()
    print("kitchen agent running. Ctrl-C to stop.")
    while True:
        try:
            d = get_active()
            active = d.get("active", [])
            vol = d.get("box_volume", 100) / 100.0
            live = {a["token"] for a in active}
            shorts = [a for a in active if not _is_song(a)]
            songs  = [a for a in active if _is_song(a)]
            # short sounds — overlap on channels
            for a in shorts:
                if a["token"] not in _playing:
                    play_short(a, vol)
                else:
                    try: _playing[a["token"]][0].set_volume(vol)
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
                print("loop error:", e); time.sleep(1.5)
        except Exception as e:
            print("loop error:", e); time.sleep(1.5)
        time.sleep(POLL)

if __name__ == "__main__":
    run()
