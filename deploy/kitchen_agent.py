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
import os, time, json, hashlib, urllib.request, urllib.parse, http.cookiejar

SERVER   = os.environ.get("SS_SERVER", "https://sounderserver.party")
PASSWORD = os.environ.get("SS_PASSWORD", "")   # set via env / the systemd unit
CACHE    = os.path.expanduser("~/kitchen_cache")
POLL     = 0.35
os.makedirs(CACHE, exist_ok=True)

os.environ.setdefault("SDL_AUDIODRIVER", os.environ.get("SS_DRIVER", "alsa"))
if os.environ["SDL_AUDIODRIVER"] == "alsa":
    os.environ.setdefault("AUDIODEV", os.environ.get("SS_AUDIODEV", "hw:3,0"))

import pygame
pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
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

def fetch(fn):
    ext = os.path.splitext(fn)[1] or ".wav"
    path = os.path.join(CACHE, hashlib.md5(fn.encode()).hexdigest() + ext)
    if not os.path.exists(path):
        url = SERVER + "/api/audio?f=" + urllib.parse.quote(fn)
        data = _opener.open(url, timeout=60).read()
        tmp = path + ".tmp"
        open(tmp, "wb").write(data); os.replace(tmp, path)
    return path

_playing = {}   # token -> (channel, Sound)

def play(entry, vol):
    tok = entry["token"]
    if tok in _playing:
        return
    try:
        snd = pygame.mixer.Sound(fetch(entry["file"]))
        snd.set_volume(max(0.0, min(1.0, vol)))
        ch = pygame.mixer.find_channel(True)   # steal oldest if all busy
        ch.play(snd)
        _playing[tok] = (ch, snd)
        print("▶", entry.get("name"), "lane", entry.get("lane"), "by", entry.get("by"))
    except Exception as e:
        print("play error:", entry.get("file"), e)

def run():
    print("logging in to", SERVER, "...")
    me = login(); print("  ->", me)
    print("kitchen agent running. Ctrl-C to stop.")
    while True:
        try:
            d = get_active()
            active = d.get("active", [])
            vol = d.get("box_volume", 100) / 100.0
            live = {a["token"] for a in active}
            for a in active:
                if a["token"] not in _playing:
                    play(a, vol)
                else:
                    try: _playing[a["token"]][0].set_volume(vol)
                    except Exception: pass
            for tok in list(_playing):
                if tok not in live:                 # interrupted/finished on the server
                    ch, _ = _playing.pop(tok)
                    try: ch.stop()
                    except Exception: pass
        except Exception as e:
            print("loop error:", e); time.sleep(1.5)
        time.sleep(POLL)

if __name__ == "__main__":
    run()
