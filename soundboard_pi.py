#!/usr/bin/env python3
"""
Raspberry Pi Soundboard
Physical GPIO buttons + Web UI, all through one pygame mixer.
"""

import os
import sys
import json
import time
import random
import logging
import subprocess
import numpy as np
import threading
from signal import pause
from datetime import datetime

try:
    import pygame
    import sounddevice as sd
    from gpiozero import Button, LED
except ImportError as e:
    print(f"Missing dependency: {e}")
    sys.exit(1)

from flask import Flask, render_template, jsonify, request

# =============================================================================
# LOGGING
# =============================================================================

LOG_FILE = "/home/pi/soundboard/soundboard.log"
logger = logging.getLogger("soundboard")
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler(LOG_FILE)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(fh)
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(sh)

# Suppress Flask request logging noise
werkzeug_log = logging.getLogger('werkzeug')
werkzeug_log.setLevel(logging.WARNING)

# =============================================================================
# CONFIGURATION
# =============================================================================

RECORD_BUTTON_PIN = 4

SOUND_BUTTON_PINS = {
    1: 27,
    2: 22,
    3: 23,
    4: 24,
    5: 25,
    6: 5,
    7: 6,
    8: 13,
}

RECORD_LED_PIN = None

SOUND_DIR = "/home/pi/soundboard/sounds"
BACKUP_DIR = "/home/pi/soundboard/sounds/backups"
SAMPLE_RATE = 44100
NUM_CHANNELS = 32
WEB_CHANNEL = 15  # primary song channel
SFX_CHANNEL = 14  # (legacy) first sfx channel
SONG_CHANNELS = [15, 13]                                      # up to 2 concurrent songs
SFX_POOL = [14, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31]  # overlapping sfx/dcc

CYCLE_WINDOW = 10  # seconds to re-press for cycling

# 5-digit GGSSS scheme: GG = group ID, SSS = song position
GROUPS_FILE = "/home/pi/soundboard/groups.json"

DEFAULT_GROUPS = [
    ("Meme / Misc",      1001,  1999),
    ("Persona 3",        2001,  2999),
    ("Persona 4",        3001,  3999),
    ("Persona 5",        4001,  4999),
    ("Anime OPs",        5001,  5999),
    ("Initial D",        6001,  6999),
    ("Phonk",            7001,  7999),
    ("Synthwave",        8001,  8999),
    ("NieR",             9001,  9999),
    ("Undertale",       10001, 10999),
    ("Final Fantasy",   11001, 11999),
    ("Zelda",           12001, 12999),
    ("Attack on Titan", 13001, 13999),
    ("Game Bangers",    14001, 14999),
    ("Mewgenics",       15001, 15999),
    ("Hollow Knight",   16001, 16999),
    ("Hotline Miami",   17001, 17999),
    ("Animal Crossing", 18001, 18999),
    ("Vaporwave",       19001, 19999),
    ("Electronic",      20001, 20999),
    ("City Pop",        21001, 21999),
    ("Pop / Party",     22001, 22999),
    ("Katherine Bops",  23001, 23999),
    ("Classic Rock",    24001, 24999),
    ("Disney",          25001, 25999),
]


def load_groups():
    """Load groups from JSON file, falling back to defaults. Sorted alphabetically."""
    import json
    if os.path.exists(GROUPS_FILE):
        try:
            with open(GROUPS_FILE) as f:
                data = json.load(f)
            groups = [(g["name"], g["start"], g["end"]) for g in data]
            # Meme / Misc always first, then alphabetical
            return sorted(groups, key=lambda g: (0 if g[0] == "Meme / Misc" else 1, g[0].lower()))
        except Exception:
            pass
    # First run or corrupt file — save defaults
    save_groups(DEFAULT_GROUPS)
    return sorted(DEFAULT_GROUPS, key=lambda g: (0 if g[0] == "Meme / Misc" else 1, g[0].lower()))


def save_groups(groups):
    """Save groups to JSON file."""
    import json
    data = [{"name": name, "start": start, "end": end} for name, start, end in groups]
    with open(GROUPS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def create_new_group(name):
    """Create a new group with the next available GG prefix. Returns (name, start, end)."""
    global SLOT1_GROUPS
    # Find the highest GG number
    max_gg = max(start // 1000 for _, start, _ in SLOT1_GROUPS)
    new_gg = max_gg + 1
    new_group = (name, new_gg * 1000 + 1, new_gg * 1000 + 999)
    SLOT1_GROUPS.append(new_group)
    save_groups(SLOT1_GROUPS)
    # Reload slot1 sounds to pick up new group
    load_slot1_sounds()
    logger.info(f"📁 New group created: {name} ({new_gg}xxx)")
    return new_group


SLOT1_GROUPS = load_groups()

WEB_PORT = 5000
WEB_TEMPLATE_DIR = "/home/pi/soundboard/web/templates"
SFX_DIR = os.path.join(SOUND_DIR, "sfx")
USAGE_FILE = "/home/pi/soundboard/usage.json"

# =============================================================================
# STATE
# =============================================================================

is_recording = False
recording_slot = None
current_recording = []
stream = None
sounds = {}
channels = {}

record_button = None
sound_buttons = {}
record_led = None

# Slot 1 group-based state
slot1_folder = os.path.join(SOUND_DIR, "sound_1")
slot1_current_file = os.path.join(SOUND_DIR, "sound_1", ".current")
slot1_all_files = []
slot1_groups = {}
slot1_group_names = []
slot1_group_index = 0
slot1_song_index = 0
slot1_sound = None
slot1_last_press = 0.0
slot1_playing = False

# Slot 2 random clips state
slot2_folder = os.path.join(SOUND_DIR, "sound_2")
slot2_clips = []
slot2_sound = None

# Slot 8 DCC quote state
slot8_folder = os.path.join(SOUND_DIR, "sound_8")
slot8_categories = []
slot8_cat_index = 0
slot8_clips = {}
slot8_sound = None
slot8_all_clip = None

slot1_gdd_clip = None

# Web playback state
web_sound = None        # currently loaded web pygame.mixer.Sound
web_channel = None      # pygame channel for web playback
shuffle_thread = None   # background shuffle thread
shuffle_stop = False    # flag to stop shuffle
shuffle_skip = False    # flag to skip current track in shuffle
shuffle_active = False  # whether shuffle is currently running
web_now_playing = ""    # display name of current track
web_now_playing_path = ""  # filesystem path of current web track (browser stream mirror)
web_play_start = 0      # time.time() when current track started
web_play_duration = 0   # duration of current track in seconds
web_paused = False      # whether playback is paused
web_pause_elapsed = 0   # elapsed time when paused

# SFX playback state (separate channel, doesn't interrupt songs)
sfx_sound = None
sfx_channel = None
sfx_now_playing_path = ""   # current sfx file (event-driven browser audio)
sfx_play_start = 0          # time.time() when current sfx started
sfx_now_playing_name = ""   # display name of current sfx

# --- Active-sound registry: supports overlapping playback + browser layering ---
_ACTIVE = {}                 # token -> {ch, ch_idx, sound, path, name, by, kind, start, duration, paused}
_ACTIVE_LOCK = threading.Lock()
_PLAY_SEQ = 0


def _trigger_name():
    """Best-effort display name of the requesting user (or '')."""
    try:
        from flask import session as _s
        return (_s.get("name") or "").strip()
    except Exception:
        return ""


def _trigger_sid():
    """Stable per-session id of the requesting user (or '')."""
    try:
        from flask import session as _s
        return _s.get("sid") or ""
    except Exception:
        return ""


def _prune_active():
    with _ACTIVE_LOCK:
        for tok in list(_ACTIVE.keys()):
            e = _ACTIVE[tok]
            try:
                busy = e["ch"].get_busy()
            except Exception:
                busy = False
            if not busy and not e.get("paused"):
                _ACTIVE.pop(tok, None)


def _register_active(ch_idx, channel, sound, path, name, by, kind, duration):
    global _PLAY_SEQ
    with _ACTIVE_LOCK:
        _PLAY_SEQ += 1
        tok = _PLAY_SEQ
        _ACTIVE[tok] = {"ch": channel, "ch_idx": ch_idx, "sound": sound, "path": path,
                        "name": name, "by": by, "sid": _trigger_sid(), "kind": kind, "start": time.time(),
                        "duration": duration, "paused": False}
    return tok


# --- Per-person play caps (admin-configurable, persisted) ---
_LIMITS_FILE = os.path.join(os.path.dirname(SOUND_DIR), "limits.json")
_LIMITS = {"songs_per_person": 2, "sfx_per_person": 3}


def _load_limits():
    try:
        with open(_LIMITS_FILE) as f:
            d = json.load(f)
        _LIMITS["songs_per_person"] = int(d.get("songs_per_person", 2))
        _LIMITS["sfx_per_person"] = int(d.get("sfx_per_person", 3))
    except Exception:
        pass


def _save_limits():
    try:
        tmp = _LIMITS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_LIMITS, f)
        os.replace(tmp, _LIMITS_FILE)
    except Exception:
        pass


def _enforce_user_cap(sid, kind, cap):
    """Stop this user's oldest sound(s) of `kind` so adding one keeps them within `cap`."""
    if not sid:
        return
    _prune_active()
    while True:
        with _ACTIVE_LOCK:
            mine = sorted((e["start"], t, e["ch_idx"]) for t, e in _ACTIVE.items()
                          if e.get("sid") == sid and e["kind"] == kind)
        if len(mine) < cap:
            return
        _, oldtok, oldidx = mine[0]
        try:
            pygame.mixer.Channel(oldidx).stop()
        except Exception:
            pass
        with _ACTIVE_LOCK:
            _ACTIVE.pop(oldtok, None)


_load_limits()


def _active_list():
    _prune_active()
    with _ACTIVE_LOCK:
        return [{"token": t, "name": e["name"], "by": e["by"], "kind": e["kind"],
                 "paused": bool(e.get("paused")), "start": e["start"], "duration": e.get("duration", 0),
                 "src": _sound_key(e["path"])}
                for t, e in sorted(_ACTIVE.items())]


def _pick_song_channel():
    _prune_active()
    with _ACTIVE_LOCK:
        used = {e["ch_idx"] for e in _ACTIVE.values() if e["kind"] == "song"}
    for ci in SONG_CHANNELS:
        if ci not in used:
            return ci
    with _ACTIVE_LOCK:
        songs = sorted((e["start"], t, e["ch_idx"]) for t, e in _ACTIVE.items() if e["kind"] == "song")
    if songs:
        _, oldtok, oldidx = songs[0]
        try:
            pygame.mixer.Channel(oldidx).stop()
        except Exception:
            pass
        with _ACTIVE_LOCK:
            _ACTIVE.pop(oldtok, None)
        return oldidx
    return SONG_CHANNELS[0]


def _pick_sfx_channel():
    _prune_active()
    with _ACTIVE_LOCK:
        used = {e["ch_idx"] for e in _ACTIVE.values() if e["kind"] == "sfx"}
    for ci in SFX_POOL:
        if ci not in used:
            try:
                if not pygame.mixer.Channel(ci).get_busy():
                    return ci
            except Exception:
                return ci
    with _ACTIVE_LOCK:
        sfxs = sorted((e["start"], t, e["ch_idx"]) for t, e in _ACTIVE.items() if e["kind"] == "sfx")
    if sfxs:
        _, oldtok, oldidx = sfxs[0]
        try:
            pygame.mixer.Channel(oldidx).stop()
        except Exception:
            pass
        with _ACTIVE_LOCK:
            _ACTIVE.pop(oldtok, None)
        return oldidx
    return SFX_POOL[0]

# =============================================================================
# AUDIO FUNCTIONS
# =============================================================================

def log_usage(filename, source="web"):
    """Log a play event for usage tracking."""
    import json
    try:
        if os.path.exists(USAGE_FILE):
            with open(USAGE_FILE) as f:
                usage = json.load(f)
        else:
            usage = {}
        if filename not in usage:
            usage[filename] = {"count": 0, "last_played": ""}
        usage[filename]["count"] += 1
        usage[filename]["last_played"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        usage[filename]["source"] = source
        with open(USAGE_FILE, 'w') as f:
            json.dump(usage, f)
    except Exception:
        pass


def ensure_sound_dir():
    if not os.path.exists(SOUND_DIR):
        os.makedirs(SOUND_DIR)
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)


def get_sound_path(slot: int) -> str:
    return os.path.join(SOUND_DIR, f"sound_{slot}.wav")


def backup_sound(slot: int):
    import shutil
    path = get_sound_path(slot)
    if os.path.exists(path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"sound_{slot}_{timestamp}.wav"
        backup_path = os.path.join(BACKUP_DIR, backup_name)
        shutil.copy(path, backup_path)
        logger.info(f"📁 Backed up to {backup_name}")


def get_file_number(filename):
    try:
        parts = filename.split('_')
        return int(parts[1])
    except (IndexError, ValueError):
        return -1


def get_display_name(filename):
    name = filename.replace('.wav', '')
    parts = name.split('_')
    if len(parts) >= 3:
        display = ' '.join(parts[2:])
    else:
        display = '_'.join(parts[1:])
    return display.replace('_', ' ').title()


def slot1_load_sound_by_name(filename):
    global slot1_sound
    if slot1_sound is not None:
        del slot1_sound
        slot1_sound = None
    path = os.path.join(slot1_folder, filename)
    try:
        slot1_sound = pygame.mixer.Sound(path)
        return True
    except Exception as e:
        logger.info(f"  Slot 1: failed to load {filename} - {e}")
        return False


def load_slot1_sounds():
    global slot1_all_files, slot1_groups, slot1_group_names
    global slot1_group_index, slot1_song_index, slot1_gdd_clip

    if not os.path.isdir(slot1_folder):
        return False

    wav_files = sorted([
        f for f in os.listdir(slot1_folder)
        if f.endswith(".wav") and ".tmp." not in f
    ])

    if not wav_files:
        logger.info("  Slot 1 folder: empty")
        return False

    slot1_all_files = wav_files
    logger.info(f"  Slot 1: found {len(slot1_all_files)} sounds")

    slot1_groups = {}
    for group_name, start, end in SLOT1_GROUPS:
        group_files = [f for f in wav_files if start <= get_file_number(f) <= end]
        if group_files:
            slot1_groups[group_name] = group_files
            logger.info(f"  Slot 1 [{group_name}]: {len(group_files)} songs")

    slot1_groups["all"] = wav_files
    slot1_group_names = [name for name, _, _ in SLOT1_GROUPS if name in slot1_groups] + ["all"]
    logger.info(f"  Slot 1: groups: {slot1_group_names}")

    slot8_carl = os.path.join(SOUND_DIR, "sound_8", "carl")
    if os.path.isdir(slot8_carl):
        for f in os.listdir(slot8_carl):
            if "god_damn_it_donut" in f:
                slot1_gdd_clip = os.path.join(slot8_carl, f)
                logger.info(f"  Slot 1: 'all' identifier clip found")
                break

    slot1_group_index = 0
    slot1_song_index = 0
    if os.path.exists(slot1_current_file):
        try:
            saved = open(slot1_current_file).read().strip()
            if ':' in saved:
                saved_group, saved_file = saved.split(':', 1)
                if saved_group in slot1_groups:
                    slot1_group_index = slot1_group_names.index(saved_group)
                    group_files = slot1_groups[saved_group]
                    if saved_file in group_files:
                        slot1_song_index = group_files.index(saved_file)
                    logger.info(f"  Slot 1: restored → [{saved_group}] {saved_file}")
            elif saved in wav_files:
                slot1_song_index = 0
                for gname in slot1_group_names:
                    if saved in slot1_groups[gname]:
                        slot1_group_index = slot1_group_names.index(gname)
                        slot1_song_index = slot1_groups[gname].index(saved)
                        break
                logger.info(f"  Slot 1: restored (legacy) → {saved}")
        except Exception:
            pass

    group = slot1_group_names[slot1_group_index]
    current_file = slot1_groups[group][slot1_song_index]
    slot1_load_sound_by_name(current_file)
    logger.info(f"  Slot 1: current → [{group}] {current_file}")
    return True


def save_slot1_selection():
    group = slot1_group_names[slot1_group_index]
    files = slot1_groups[group]
    if files and slot1_song_index < len(files):
        try:
            with open(slot1_current_file, 'w') as f:
                f.write(f"{group}:{files[slot1_song_index]}")
        except Exception:
            pass


def slot1_playback_monitor():
    global slot1_playing
    while True:
        if slot1_playing and 1 in channels and not channels[1].get_busy():
            slot1_playing = False
        time.sleep(0.5)


def load_slot2_sounds():
    """Discover clips in sound_2/ folder for random playback."""
    global slot2_clips
    if not os.path.isdir(slot2_folder):
        return False
    wavs = sorted([
        os.path.join(slot2_folder, f)
        for f in os.listdir(slot2_folder)
        if f.endswith(".wav") and ".tmp." not in f
    ])
    if not wavs:
        logger.info("  Slot 2 folder: empty")
        return False
    slot2_clips = wavs
    logger.info(f"  Slot 2: {len(slot2_clips)} clips (random mode)")
    return True


def load_slot8_sounds():
    global slot8_categories, slot8_clips, slot8_all_clip

    if not os.path.isdir(slot8_folder):
        return False

    subdirs = sorted([
        d for d in os.listdir(slot8_folder)
        if os.path.isdir(os.path.join(slot8_folder, d))
    ])

    if not subdirs:
        logger.info("  Slot 8: no character subfolders found")
        return False

    slot8_clips = {}
    all_clips = []
    for d in subdirs:
        dirpath = os.path.join(slot8_folder, d)
        wavs = sorted([
            os.path.join(dirpath, f)
            for f in os.listdir(dirpath)
            if f.endswith(".wav") and ".tmp." not in f
        ])
        if wavs:
            slot8_clips[d] = wavs
            all_clips.extend(wavs)
            logger.info(f"  Slot 8 [{d}]: {len(wavs)} clips")

    if not all_clips:
        logger.info("  Slot 8: no clips found in subfolders")
        return False

    root_wavs = [
        os.path.join(slot8_folder, f)
        for f in os.listdir(slot8_folder)
        if f.endswith(".wav") and os.path.isfile(os.path.join(slot8_folder, f))
    ]
    if root_wavs:
        slot8_all_clip = root_wavs[0]
        all_clips.extend(root_wavs)
        logger.info(f"  Slot 8: 'all' identifier clip: {os.path.basename(slot8_all_clip)}")

    slot8_clips["all"] = all_clips
    slot8_categories = list(slot8_clips.keys())
    logger.info(f"  Slot 8: {len(all_clips)} total clips, categories: {slot8_categories}")
    return True


def load_existing_sounds():
    logger.info("Loading existing sounds...")

    if not load_slot1_sounds():
        path = get_sound_path(1)
        if os.path.exists(path):
            try:
                sounds[1] = pygame.mixer.Sound(path)
                logger.info(f"  Slot 1: loaded (single file)")
            except Exception as e:
                logger.info(f"  Slot 1: failed - {e}")
        else:
            logger.info(f"  Slot 1: empty")

    # Slot 2: folder-based random playback
    if not load_slot2_sounds():
        path = get_sound_path(2)
        if os.path.exists(path):
            try:
                sounds[2] = pygame.mixer.Sound(path)
                logger.info(f"  Slot 2: loaded (single file)")
            except Exception as e:
                logger.info(f"  Slot 2: failed - {e}")
        else:
            logger.info(f"  Slot 2: empty")

    # Slots 3-7: single file
    for slot in range(3, 8):
        path = get_sound_path(slot)
        if os.path.exists(path):
            try:
                sounds[slot] = pygame.mixer.Sound(path)
                logger.info(f"  Slot {slot}: loaded")
            except Exception as e:
                logger.info(f"  Slot {slot}: failed - {e}")
        else:
            logger.info(f"  Slot {slot}: empty")

    if not load_slot8_sounds():
        path = get_sound_path(8)
        if os.path.exists(path):
            try:
                sounds[8] = pygame.mixer.Sound(path)
                logger.info(f"  Slot 8: loaded (single file)")
            except Exception as e:
                logger.info(f"  Slot 8: failed - {e}")
        else:
            logger.info(f"  Slot 8: empty")


def start_recording(slot: int):
    global is_recording, recording_slot, current_recording, stream
    logger.debug(f"start_recording called for slot {slot}")
    is_recording = True
    recording_slot = slot
    current_recording = []
    if record_led:
        record_led.on()
    logger.info(f"🔴 Recording to slot {slot}...")
    def callback(indata, frames, time, status):
        current_recording.append(indata.copy())
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, device="C-Media USB Audio Device", callback=callback)
    stream.start()
    logger.debug("Stream started")


def stop_recording():
    global is_recording, sounds, recording_slot, stream, current_recording
    logger.debug(f"stop_recording called, is_recording={is_recording}")
    if not is_recording:
        return
    slot = recording_slot
    is_recording = False
    recording_slot = None
    if record_led:
        record_led.off()
    stream.stop()
    stream.close()
    logger.debug(f"Chunks captured: {len(current_recording)}")
    if not current_recording:
        logger.warning("No audio recorded")
        return
    logger.info("Processing...")
    backup_sound(slot)
    audio = np.concatenate(current_recording, axis=0)
    audio = (audio * 32767).astype(np.int16)
    path = get_sound_path(slot)
    import wave
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    sounds[slot] = pygame.mixer.Sound(path)
    logger.info(f"✅ Saved to slot {slot}")


# =============================================================================
# WEB PLAYBACK (through pygame)
# =============================================================================

def web_play_file(filepath):
    """Play a song. Up to 2 play at once (across SONG_CHANNELS); a 3rd bumps the oldest."""
    global web_sound, web_channel, shuffle_stop, web_now_playing, web_now_playing_path, web_play_start, web_play_duration, web_paused
    shuffle_stop = True  # stop any running shuffle
    web_paused = False
    try:
        _enforce_user_cap(_trigger_sid(), "song", _LIMITS["songs_per_person"])
        ci = _pick_song_channel()
        channel = pygame.mixer.Channel(ci)
        snd = pygame.mixer.Sound(filepath)
        channel.set_volume(current_volume)
        channel.play(snd)
        dur = snd.get_length()
        _register_active(ci, channel, snd, filepath,
                         get_display_name(os.path.basename(filepath)), _trigger_name(), "song", dur)
        # legacy single-state (most recent song) for /api/status + pause
        web_sound = snd
        web_channel = channel
        web_play_duration = dur
        web_play_start = time.time()
        web_now_playing = get_display_name(os.path.basename(filepath))
        web_now_playing_path = filepath
        log_usage(os.path.basename(filepath), "web")
        _log_event("played a song", get_display_name(os.path.basename(filepath)))
        logger.info(f"🌐 Web play [ch {ci}]: {os.path.basename(filepath)}")
        return True
    except Exception as e:
        logger.warning(f"🌐 Web play failed: {filepath} - {e}")
        web_now_playing = ""
        return False


def sfx_play_file(filepath):
    """Play an SFX/DCC clip on its own channel — overlaps everything, any number."""
    global sfx_sound, sfx_channel, sfx_now_playing_path, sfx_play_start, sfx_now_playing_name
    try:
        _enforce_user_cap(_trigger_sid(), "sfx", _LIMITS["sfx_per_person"])
        ci = _pick_sfx_channel()
        channel = pygame.mixer.Channel(ci)
        snd = pygame.mixer.Sound(filepath)
        channel.set_volume(current_volume)
        channel.play(snd)
        _register_active(ci, channel, snd, filepath,
                         get_display_name(os.path.basename(filepath)), _trigger_name(), "sfx", snd.get_length())
        sfx_sound = snd
        sfx_channel = channel
        sfx_now_playing_path = filepath
        sfx_play_start = time.time()
        sfx_now_playing_name = get_display_name(os.path.basename(filepath))
        log_usage(os.path.basename(filepath), "sfx")
        _log_event("played an sfx", get_display_name(os.path.basename(filepath)))
        logger.info(f"🔊 SFX [ch {ci}]: {os.path.basename(filepath)}")
        return True
    except Exception as e:
        logger.warning(f"🔊 SFX failed: {filepath} - {e}")
        return False


def web_stop():
    """Stop ALL songs and sfx (clears the board)."""
    global shuffle_stop, web_now_playing, web_now_playing_path, web_paused
    shuffle_stop = True
    web_paused = False
    for ci in SONG_CHANNELS + SFX_POOL:
        try:
            pygame.mixer.Channel(ci).stop()
        except Exception:
            pass
    with _ACTIVE_LOCK:
        _ACTIVE.clear()
    web_now_playing = ""
    web_now_playing_path = ""
    _log_event("stopped everything")
    logger.info("🌐 Stop all")


def web_shuffle_play(filepaths):
    """Play a list of files sequentially through pygame in a background thread."""
    global shuffle_stop, shuffle_skip, shuffle_thread, web_sound, web_channel, shuffle_active

    shuffle_stop = True
    time.sleep(0.1)  # let previous shuffle stop
    shuffle_stop = False
    shuffle_skip = False
    shuffle_active = True

    def _play():
        global web_sound, shuffle_stop, shuffle_skip, shuffle_active, web_now_playing, web_now_playing_path, web_play_start, web_play_duration
        if web_channel is None:
            return
        for fp in filepaths:
            if shuffle_stop:
                break
            shuffle_skip = False
            try:
                new_sound = pygame.mixer.Sound(fp)
                # Only free previous sound after new one is loaded
                if web_sound is not None:
                    del web_sound
                web_sound = new_sound
                web_play_duration = web_sound.get_length()
                web_play_start = time.time()
                web_now_playing = get_display_name(os.path.basename(fp))
                web_now_playing_path = fp
                web_channel.play(web_sound)
                logger.info(f"🌐 Shuffle: {os.path.basename(fp)}")
                # Wait for playback to start
                time.sleep(0.5)
                # Wait for it to finish
                while (web_channel.get_busy() or web_paused) and not shuffle_stop and not shuffle_skip:
                    time.sleep(0.3)
                if shuffle_skip:
                    web_channel.stop()
                    logger.info(f"⏭ Skipped: {os.path.basename(fp)}")
                # Small gap between songs
                if not shuffle_stop:
                    time.sleep(0.3)
            except Exception as e:
                logger.warning(f"🌐 Shuffle failed: {fp} - {e}")
        shuffle_active = False
        web_now_playing = ""
        logger.info("🌐 Shuffle finished")

    shuffle_thread = threading.Thread(target=_play, daemon=True)
    shuffle_thread.start()


# =============================================================================
# GPIO PLAY FUNCTIONS
# =============================================================================

def play_sound(slot: int):
    global slot1_last_press, slot1_song_index, slot1_playing

    # Slot 2: random clip from folder
    if slot == 2 and slot2_clips:
        global slot2_sound
        pick = random.choice(slot2_clips)
        if slot2_sound is not None:
            del slot2_sound
            slot2_sound = None
        try:
            slot2_sound = pygame.mixer.Sound(pick)
            if 2 not in channels:
                channels[2] = pygame.mixer.Channel(2)
            channels[2].stop()
            channels[2].play(slot2_sound)
            logger.info(f"🎲 Slot 2: {os.path.basename(pick)}")
        except Exception as e:
            logger.warning(f"Slot 2: failed to play {pick} - {e}")
        return

    if slot == 1 and slot1_group_names:
        now = time.time()
        elapsed = now - slot1_last_press
        slot1_last_press = now

        group = slot1_group_names[slot1_group_index]
        files = slot1_groups[group]

        if elapsed < CYCLE_WINDOW and 1 in channels and channels[1].get_busy():
            channels[1].stop()
            if group == "all":
                pick = random.choice(slot1_all_files)
                slot1_load_sound_by_name(pick)
                if slot1_sound:
                    channels[1].play(slot1_sound)
                    slot1_playing = True
                    logger.info(f"🎲 Slot 1 [all]: {pick}")
            else:
                slot1_song_index = (slot1_song_index + 1) % len(files)
                slot1_load_sound_by_name(files[slot1_song_index])
                if slot1_sound:
                    channels[1].play(slot1_sound)
                    slot1_playing = True
                    logger.info(f"🔄 Slot 1 [{group}]: {files[slot1_song_index]}")
                save_slot1_selection()
        else:
            if 1 not in channels:
                channels[1] = pygame.mixer.Channel(1)
            channels[1].stop()
            if group == "all":
                pick = random.choice(slot1_all_files)
                slot1_load_sound_by_name(pick)
                if slot1_sound:
                    channels[1].play(slot1_sound)
                    slot1_playing = True
                    logger.info(f"🎲 Slot 1 [all]: {pick}")
            else:
                if slot1_sound:
                    channels[1].play(slot1_sound)
                    slot1_playing = True
                    logger.info(f"▶️  Slot 1 [{group}]: {files[slot1_song_index]}")
                else:
                    logger.warning(f"Slot 1: no sound loaded")
        return

    if slot == 8 and slot8_categories:
        global slot8_sound
        cat = slot8_categories[slot8_cat_index]
        pick = random.choice(slot8_clips[cat])
        if slot8_sound is not None:
            del slot8_sound
            slot8_sound = None
        try:
            slot8_sound = pygame.mixer.Sound(pick)
            if 8 not in channels:
                channels[8] = pygame.mixer.Channel(8)
            channels[8].stop()
            channels[8].play(slot8_sound)
            logger.info(f"🎲 Slot 8 [{cat}]: {os.path.basename(pick)}")
        except Exception as e:
            logger.warning(f"Slot 8: failed to play {pick} - {e}")
        return

    if slot in sounds:
        if slot not in channels:
            channels[slot] = pygame.mixer.Channel(slot)
        channels[slot].stop()
        channels[slot].play(sounds[slot])
        logger.info(f"▶️  Playing slot {slot}")
    else:
        logger.info(f"  Slot {slot} is empty")


# =============================================================================
# GPIO BUTTON HANDLERS
# =============================================================================

def slot1_cycle_group():
    global slot1_group_index, slot1_song_index, slot1_playing, slot1_sound
    if not slot1_group_names:
        return
    slot1_group_index = (slot1_group_index + 1) % len(slot1_group_names)
    group = slot1_group_names[slot1_group_index]
    slot1_song_index = 0
    if 1 not in channels:
        channels[1] = pygame.mixer.Channel(1)
    channels[1].stop()
    if group == "all" and slot1_gdd_clip:
        if slot1_sound is not None:
            del slot1_sound
            slot1_sound = None
        try:
            slot1_sound = pygame.mixer.Sound(slot1_gdd_clip)
            channels[1].play(slot1_sound)
            slot1_playing = True
            logger.info(f"🔄 Slot 1 group → [all]: god_damn_it_donut")
        except Exception as e:
            logger.warning(f"Slot 1 group cycle failed: {e}")
    else:
        files = slot1_groups[group]
        pick = random.choice(files)
        slot1_song_index = files.index(pick)
        slot1_load_sound_by_name(pick)
        if slot1_sound:
            channels[1].play(slot1_sound)
            slot1_playing = True
            logger.info(f"🔄 Slot 1 group → [{group}]: {pick}")
    save_slot1_selection()


def slot8_cycle_category():
    global slot8_cat_index, slot8_sound
    if not slot8_categories:
        return
    slot8_cat_index = (slot8_cat_index + 1) % len(slot8_categories)
    cat = slot8_categories[slot8_cat_index]
    if cat == "all" and slot8_all_clip:
        pick = slot8_all_clip
    else:
        pick = random.choice(slot8_clips[cat])
    if slot8_sound is not None:
        del slot8_sound
        slot8_sound = None
    try:
        slot8_sound = pygame.mixer.Sound(pick)
        if 8 not in channels:
            channels[8] = pygame.mixer.Channel(8)
        channels[8].stop()
        channels[8].play(slot8_sound)
        logger.info(f"🔄 Slot 8 category → [{cat}]: {os.path.basename(pick)}")
    except Exception as e:
        logger.warning(f"Slot 8 cycle failed: {e}")


def on_sound_button_pressed(slot: int):
    logger.debug(f"Sound button {slot} pressed, record_held={record_button.is_pressed}")
    if record_button.is_pressed:
        if slot == 1:
            slot1_cycle_group()
        elif slot == 8:
            slot8_cycle_category()
        elif not is_recording:
            start_recording(slot)
    else:
        play_sound(slot)


def on_sound_button_released(slot: int):
    logger.debug(f"Sound button {slot} released, is_recording={is_recording}, recording_slot={recording_slot}")
    if is_recording and recording_slot == slot:
        stop_recording()


def setup_gpio():
    global record_button, sound_buttons, record_led
    logger.info("Setting up GPIO...")
    record_button = Button(RECORD_BUTTON_PIN, pull_up=True, bounce_time=0.05)
    logger.info(f"  Record button on GPIO {RECORD_BUTTON_PIN} (Pin 7)")
    if RECORD_LED_PIN:
        record_led = LED(RECORD_LED_PIN)
        logger.info(f"  Record LED on GPIO {RECORD_LED_PIN}")
    for slot, pin in SOUND_BUTTON_PINS.items():
        btn = Button(pin, pull_up=True, bounce_time=0.05)
        btn.when_pressed = lambda s=slot: on_sound_button_pressed(s)
        btn.when_released = lambda s=slot: on_sound_button_released(s)
        sound_buttons[slot] = btn
        logger.info(f"  Sound button {slot} on GPIO {pin}")


# =============================================================================
# WEB SERVER (Flask, runs in a thread)
# =============================================================================

webapp = Flask(__name__, template_folder=WEB_TEMPLATE_DIR)


# --- Session login gate (password-only; password selects role) ---
import hmac as _hmac
import os as _os
import datetime as _dt
from flask import session as _session, redirect as _redirect

try:
    from secrets_config import USER_PASS as _USER_PASS, ADMIN_PASS as _ADMIN_PASS
except Exception:              # secrets_config.py is gitignored; put the REAL passwords there
    _USER_PASS, _ADMIN_PASS = "changeme-user", "changeme-admin"

try:
    from secrets_config import GMAIL_USER as _GMAIL_USER, GMAIL_APP_PASSWORD as _GMAIL_PW
except Exception:
    _GMAIL_USER, _GMAIL_PW = "", ""
_ADMIN_EMAIL = "bnowlin6@gmail.com"
_account_check_login = None


def _send_admin_email(subject, body_html):
    if not _GMAIL_USER or not _GMAIL_PW:
        logger.warning("[email] not configured; skipping send")
        return False
    try:
        import smtplib, ssl
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = _GMAIL_USER
        msg["To"] = _ADMIN_EMAIL
        msg.set_content("Open in an HTML-capable email client.")
        msg.add_alternative(body_html, subtype="html")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as srv:
            srv.login(_GMAIL_USER, _GMAIL_PW)
            srv.send_message(msg)
        logger.info("[email] sent: %s" % subject)
        return True
    except Exception as e:
        logger.warning("[email] send failed: %r" % e)
        return False

# Persistent secret key so logins survive restarts / the nightly 4am reboot.
_secret_path = "/home/pi/soundboard/.flask_secret"
try:
    if _os.path.exists(_secret_path):
        webapp.secret_key = open(_secret_path, "rb").read()
    else:
        _sk = _os.urandom(32)
        with open(_secret_path, "wb") as _f:
            _f.write(_sk)
        try:
            _os.chmod(_secret_path, 0o600)
        except Exception:
            pass
        webapp.secret_key = _sk
except Exception:
    webapp.secret_key = b"soundboard-fallback-secret-key"

webapp.permanent_session_lifetime = _dt.timedelta(days=90)
_PRESENCE = {}
_PRESENCE_TTL = 70
_RNAME_ADJ = ["Legendary", "Super", "Mysterious", "Phantom", "Turbo", "Cosmic", "Sneaky", "Rogue", "Hyper", "Shadow"]
_RNAME_NOUN = ["Saiyan", "DJ", "Goblin", "Bard", "Ronin", "Gremlin", "Maestro", "Wizard", "Bandit", "Specter"]



@webapp.before_request
def _require_login():
    p = request.path
    if p in ("/login", "/logout", "/api/register") or p.startswith("/approve/") or p.startswith("/static/"):
        return None
    if _session.get("auth"):
        _touch_presence()
        return None
    if p.startswith("/api/") or p == "/stream":
        return ("Not authenticated", 401)
    return _redirect("/login")


@webapp.before_request
def _gate_edit():
    """Add/Edit (clip studio, add, uploads) requires admin OR an approved account."""
    p = request.path
    if p.startswith("/api/clip/") or p == "/api/add" or p.startswith("/api/upload/"):
        if not (_session.get("admin") or _session.get("account_approved")):
            return ("Add/Edit requires an approved account", 403)


@webapp.route("/login", methods=["GET", "POST"])
def _login():
    err = ""
    if request.method == "POST":
        pw = (request.form.get("password") or "").strip()
        if request.form.get("mode") == "account":
            login_id = (request.form.get("login") or "").strip()
            acct = _account_check_login(login_id, pw) if _account_check_login else None
            if acct:
                _session.permanent = True
                _session["auth"] = True
                _session["admin"] = False
                _session["name"] = acct.get("username") or login_id
                _session["account"] = acct.get("email")
                _session["account_approved"] = True
                _session["sid"] = _os.urandom(8).hex()
                return _redirect("/")
            err = "No approved account matches that login + password."
        else:
            nm = (request.form.get("name") or "").strip()[:24]
            if not nm:
                nm = random.choice(_RNAME_ADJ) + " " + random.choice(_RNAME_NOUN)
            role = True if _hmac.compare_digest(pw, _ADMIN_PASS) else (False if _hmac.compare_digest(pw, _USER_PASS) else None)
            if role is not None:
                _session.permanent = True
                _session["auth"] = True
                _session["admin"] = role
                _session["name"] = nm
                _session["sid"] = _os.urandom(8).hex()
                return _redirect("/")
            err = "Nope - that's not it. (Hint: Vegeta)"
    return render_template("login.html", err=err)


@webapp.route("/logout")
def _logout():
    _session.clear()
    return _redirect("/login")


@webapp.route("/api/me")
def _api_me():
    return jsonify({"admin": bool(_session.get("admin")),
                    "can_edit": bool(_session.get("admin") or _session.get("account_approved")),
                    "user": _session.get("name") or ""})
@webapp.route("/api/presence")
def _api_presence():
    return jsonify({"users": _presence_list()})


def _touch_presence():
    try:
        sid = _session.get("sid")
        if sid:
            _PRESENCE[sid] = {"name": _session.get("name") or "", "ts": time.time()}
    except Exception:
        pass


def _presence_list():
    now = time.time()
    users = []
    for sid in list(_PRESENCE.keys()):
        v = _PRESENCE.get(sid) or {}
        if now - v.get("ts", 0) > _PRESENCE_TTL:
            _PRESENCE.pop(sid, None)
        else:
            users.append(v.get("name") or "guest")
    return users


# --- end session login gate ---


# --- Browser audio stream (decoupled file mirror; never opens the audio device) ---
try:
    import audio_stream as _audio_stream

    def _stream_state():
        try:
            busy = bool(web_channel and web_channel.get_busy())
        except Exception:
            busy = False
        return {
            "path": web_now_playing_path or None,
            "playing": busy,
            "paused": bool(web_paused),
            "start": web_play_start,
        }

    _broadcaster = _audio_stream.Broadcaster(_stream_state, logger=logger)
    _broadcaster.start()

    @webapp.route('/stream')
    def web_audio_stream():
        from flask import Response
        return Response(_broadcaster.stream(), mimetype="audio/mpeg")

    logger.info("\U0001F50A Browser audio stream enabled at /stream")
except Exception as _e:
    logger.warning("\U0001F50A Browser audio stream disabled: %r" % _e)


# --- In-browser clip editor (decoupled; never opens the audio device) ---
try:
    import clip_editor as _clip_editor
    _clip_editor.init(webapp, {
        "slot1_folder": slot1_folder,
        "sfx_dir": SFX_DIR,
        "slot8_folder": slot8_folder,
        "staging_dir": os.path.join(os.path.dirname(SOUND_DIR), "staging"),
        "groups": lambda: SLOT1_GROUPS,
        "create_new_group": create_new_group,
        "load_slot1_sounds": load_slot1_sounds,
        "get_file_number": get_file_number,
        "get_display_name": get_display_name,
        "logger": logger,
    })
except Exception as _e2:
    logger.warning("clip editor disabled: %r" % _e2)


# --- Real accounts (email + admin approval) gating Add/Edit ---
try:
    import accounts as _accounts
    _account_check_login = _accounts.init(webapp, {
        "accounts_file": os.path.join(os.path.dirname(SOUND_DIR), "accounts.json"),
        "logger": logger,
        "base_url": "https://sounderserver.party",
        "admin_email": _ADMIN_EMAIL,
        "send_email": _send_admin_email,
        "is_admin": lambda: bool(_session.get("admin")),
    })
except Exception as _e3:
    logger.warning("accounts disabled: %r" % _e3)




@webapp.route('/')
def web_index():
    return render_template('index.html')


@webapp.route('/api/songs')
def api_songs():
    all_files = sorted([
        f for f in os.listdir(slot1_folder)
        if f.endswith(".wav") and ".tmp." not in f and os.path.isfile(os.path.join(slot1_folder, f))
    ])
    groups = []
    for group_name, start, end in SLOT1_GROUPS:
        songs = []
        for f in all_files:
            num = get_file_number(f)
            if start <= num <= end:
                songs.append({"filename": f, "number": num, "display": get_display_name(f)})
        if songs:
            groups.append({"name": group_name, "songs": songs})
    # Add "All" group
    all_songs = [{"filename": f, "number": get_file_number(f), "display": get_display_name(f)} for f in all_files]
    if all_songs:
        groups.append({"name": "All", "songs": all_songs})
    return jsonify({"groups": groups})


@webapp.route('/api/dcc/categories')
def api_dcc_categories():
    cats = []
    total = 0
    for d in sorted(os.listdir(slot8_folder)):
        dirpath = os.path.join(slot8_folder, d)
        if os.path.isdir(dirpath):
            count = len([f for f in os.listdir(dirpath) if f.endswith(".wav")])
            if count > 0:
                cats.append({"name": d, "count": count})
                total += count
    cats.append({"name": "all", "count": total})
    return jsonify({"categories": cats})


@webapp.route('/api/dcc/clips/<category>')
def api_dcc_clips(category):
    clips = []
    if category == "all":
        for d in os.listdir(slot8_folder):
            dirpath = os.path.join(slot8_folder, d)
            if os.path.isdir(dirpath):
                clips.extend([f for f in os.listdir(dirpath) if f.endswith(".wav")])
    else:
        dirpath = os.path.join(slot8_folder, category)
        if os.path.isdir(dirpath):
            clips = [f for f in os.listdir(dirpath) if f.endswith(".wav")]
    return jsonify({"clips": sorted(clips)})


@webapp.route('/api/play/sound1', methods=['POST'])
def api_play_sound1():
    data = request.json
    filename = data.get('filename', '')
    if not filename.endswith('.wav') or '/' in filename or '..' in filename:
        return jsonify({"error": "Invalid filename"}), 400
    filepath = os.path.join(slot1_folder, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    web_play_file(filepath)
    return jsonify({"status": "playing", "filename": filename})


@webapp.route('/api/play/slot/<int:slot>', methods=['POST'])
def api_play_slot(slot):
    if slot < 2 or slot > 7:
        return jsonify({"error": "Invalid slot"}), 400
    filepath = os.path.join(SOUND_DIR, f"sound_{slot}.wav")
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    sfx_play_file(filepath)  # slots 2-7 play on SFX channel, don't interrupt songs
    return jsonify({"status": "playing", "slot": slot})


@webapp.route('/api/play/dcc', methods=['POST'])
def api_play_dcc():
    data = request.json
    category = data.get('category', 'all')
    clips = []
    if category == "all":
        for d in os.listdir(slot8_folder):
            dirpath = os.path.join(slot8_folder, d)
            if os.path.isdir(dirpath):
                clips.extend([os.path.join(dirpath, f) for f in os.listdir(dirpath) if f.endswith(".wav")])
    else:
        dirpath = os.path.join(slot8_folder, category)
        if os.path.isdir(dirpath):
            clips = [os.path.join(dirpath, f) for f in os.listdir(dirpath) if f.endswith(".wav")]
    if not clips:
        return jsonify({"error": "No clips found"}), 404
    pick = random.choice(clips)
    sfx_play_file(pick)  # DCC quotes play on SFX channel, don't interrupt songs
    return jsonify({"status": "playing", "clip": os.path.basename(pick)})


@webapp.route('/api/play/dcc/clip', methods=['POST'])
def api_play_dcc_clip():
    data = request.json
    category = data.get('category', '')
    filename = data.get('filename', '')
    if not filename.endswith('.wav') or '..' in filename:
        return jsonify({"error": "Invalid filename"}), 400
    if category == "all":
        for d in os.listdir(slot8_folder):
            dirpath = os.path.join(slot8_folder, d)
            filepath = os.path.join(dirpath, filename)
            if os.path.isfile(filepath):
                sfx_play_file(filepath)  # DCC quotes don't interrupt songs
                return jsonify({"status": "playing", "filename": filename})
        return jsonify({"error": "File not found"}), 404
    else:
        filepath = os.path.join(slot8_folder, category, filename)
        if not os.path.exists(filepath):
            return jsonify({"error": "File not found"}), 404
        sfx_play_file(filepath)  # DCC quotes don't interrupt songs
        return jsonify({"status": "playing", "filename": filename})


@webapp.route('/api/play/shuffle_group', methods=['POST'])
def api_play_shuffle_group():
    data = request.json
    group_name = data.get('group', '')
    all_files = sorted([
        f for f in os.listdir(slot1_folder)
        if f.endswith(".wav") and ".tmp." not in f and os.path.isfile(os.path.join(slot1_folder, f))
    ])
    songs = []
    for gname, start, end in SLOT1_GROUPS:
        if gname == group_name:
            songs = [f for f in all_files if start <= get_file_number(f) <= end]
            break
    if group_name == "All":
        songs = all_files
    if not songs:
        return jsonify({"error": "No songs in group"}), 404
    random.shuffle(songs)
    filepaths = [os.path.join(slot1_folder, s) for s in songs]
    web_shuffle_play(filepaths)
    return jsonify({"status": "playing", "group": group_name, "count": len(songs)})


@webapp.route('/api/slot2/list')
def api_slot2_list():
    """List all slot 2 clips."""
    clips = []
    if os.path.isdir(slot2_folder):
        clips = sorted([f for f in os.listdir(slot2_folder) if f.endswith(".wav")])
    display_clips = []
    for c in clips:
        name = c.replace('.wav', '').replace('_', ' ').title()
        display_clips.append({"filename": c, "display": name})
    return jsonify({"clips": display_clips})


@webapp.route('/api/slot2/play', methods=['POST'])
def api_slot2_play():
    """Play a specific slot 2 clip or random one."""
    data = request.json
    filename = data.get('filename', 'random')
    if filename == 'random':
        clips = [f for f in os.listdir(slot2_folder) if f.endswith(".wav")] if os.path.isdir(slot2_folder) else []
        if not clips:
            return jsonify({"error": "No clips found"}), 404
        filename = random.choice(clips)
    if not filename.endswith('.wav') or '/' in filename or '..' in filename:
        return jsonify({"error": "Invalid filename"}), 400
    filepath = os.path.join(slot2_folder, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    sfx_play_file(filepath)  # plays on SFX channel, doesn't interrupt songs
    return jsonify({"status": "playing", "filename": filename})


@webapp.route('/api/sfx/categories')
def api_sfx_categories():
    """List SFX categories with clip counts."""
    if not os.path.isdir(SFX_DIR):
        return jsonify({"categories": []})
    cats = []
    total = 0
    for d in sorted(os.listdir(SFX_DIR)):
        dirpath = os.path.join(SFX_DIR, d)
        if os.path.isdir(dirpath):
            count = len([f for f in os.listdir(dirpath) if f.endswith(".wav")])
            if count > 0:
                display = d.replace('_', ' ').title()
                cats.append({"name": d, "display": display, "count": count})
                total += count
    # Also count any root-level wav files
    root_count = len([f for f in os.listdir(SFX_DIR) if f.endswith(".wav") and os.path.isfile(os.path.join(SFX_DIR, f))])
    if root_count > 0:
        cats.append({"name": "_root", "display": "Other", "count": root_count})
        total += root_count
    return jsonify({"categories": cats, "total": total})


@webapp.route('/api/sfx/list')
@webapp.route('/api/sfx/list/<category>')
def api_sfx_list(category=None):
    """List sound effects, optionally filtered by category."""
    if not os.path.isdir(SFX_DIR):
        return jsonify({"clips": []})

    clips = []
    if category and category != '_root':
        catdir = os.path.join(SFX_DIR, category)
        if os.path.isdir(catdir):
            for f in sorted(os.listdir(catdir)):
                if f.endswith(".wav"):
                    name = f.replace('.wav', '').replace('sfx_', '').replace('_', ' ').title()
                    clips.append({"filename": f, "display": name, "category": category})
    elif category == '_root':
        for f in sorted(os.listdir(SFX_DIR)):
            if f.endswith(".wav") and os.path.isfile(os.path.join(SFX_DIR, f)):
                name = f.replace('.wav', '').replace('sfx_', '').replace('_', ' ').title()
                clips.append({"filename": f, "display": name, "category": "_root"})
    else:
        # All categories
        for d in sorted(os.listdir(SFX_DIR)):
            dirpath = os.path.join(SFX_DIR, d)
            if os.path.isdir(dirpath):
                for f in sorted(os.listdir(dirpath)):
                    if f.endswith(".wav"):
                        name = f.replace('.wav', '').replace('sfx_', '').replace('_', ' ').title()
                        clips.append({"filename": f, "display": name, "category": d})
        for f in sorted(os.listdir(SFX_DIR)):
            if f.endswith(".wav") and os.path.isfile(os.path.join(SFX_DIR, f)):
                name = f.replace('.wav', '').replace('sfx_', '').replace('_', ' ').title()
                clips.append({"filename": f, "display": name, "category": "_root"})
    try:
        with open(USAGE_FILE) as _uf:
            _usage = json.load(_uf)
    except Exception:
        _usage = {}
    for _c in clips:
        _c["count"] = _usage.get(_c["filename"], {}).get("count", 0)
    return jsonify({"clips": clips})


@webapp.route('/api/sfx/play', methods=['POST'])
def api_sfx_play():
    """Play a specific SFX or random one. Uses separate channel — doesn't interrupt songs."""
    data = request.json
    filename = data.get('filename', '')
    category = data.get('category', '')

    if filename == 'random':
        # Collect all clips, optionally filtered by category
        clips = []
        if category and category != '_root':
            catdir = os.path.join(SFX_DIR, category)
            if os.path.isdir(catdir):
                clips = [(catdir, f) for f in os.listdir(catdir) if f.endswith(".wav")]
        else:
            for d in os.listdir(SFX_DIR):
                dirpath = os.path.join(SFX_DIR, d)
                if os.path.isdir(dirpath):
                    clips.extend([(dirpath, f) for f in os.listdir(dirpath) if f.endswith(".wav")])
            clips.extend([(SFX_DIR, f) for f in os.listdir(SFX_DIR) if f.endswith(".wav") and os.path.isfile(os.path.join(SFX_DIR, f))])
        if not clips:
            return jsonify({"error": "No SFX found"}), 404
        dirpath, filename = random.choice(clips)
        filepath = os.path.join(dirpath, filename)
    else:
        if not filename.endswith('.wav') or '..' in filename:
            return jsonify({"error": "Invalid filename"}), 400
        # Search in category first, then all subdirs
        filepath = None
        if category and category != '_root':
            fp = os.path.join(SFX_DIR, category, filename)
            if os.path.exists(fp):
                filepath = fp
        if not filepath:
            for d in os.listdir(SFX_DIR):
                fp = os.path.join(SFX_DIR, d, filename)
                if os.path.isfile(fp):
                    filepath = fp
                    break
        if not filepath:
            fp = os.path.join(SFX_DIR, filename)
            if os.path.isfile(fp):
                filepath = fp
        if not filepath:
            return jsonify({"error": "File not found"}), 404

    sfx_play_file(filepath)
    return jsonify({"status": "playing", "filename": filename})


@webapp.route('/api/pause', methods=['POST'])
def api_pause():
    """Toggle pause/resume."""
    global web_paused, web_pause_elapsed, web_play_start
    if web_paused:
        for ci in SONG_CHANNELS:
            try:
                pygame.mixer.Channel(ci).unpause()
            except Exception:
                pass
        web_paused = False
        web_play_start = time.time() - web_pause_elapsed
        with _ACTIVE_LOCK:
            for e in _ACTIVE.values():
                if e["kind"] == "song":
                    e["paused"] = False
        _log_event("resumed playback")
        logger.info("🌐 Resumed")
        return jsonify({"status": "resumed"})
    else:
        web_pause_elapsed = time.time() - web_play_start
        for ci in SONG_CHANNELS:
            try:
                pygame.mixer.Channel(ci).pause()
            except Exception:
                pass
        web_paused = True
        with _ACTIVE_LOCK:
            for e in _ACTIVE.values():
                if e["kind"] == "song":
                    e["paused"] = True
        _log_event("paused playback")
        logger.info("🌐 Paused")
        return jsonify({"status": "paused"})


# Software volume (0.0 to 1.0) — DAC has no hardware volume control
current_volume = 0.1  # default 10% on boot

def apply_volume():
    """Apply current_volume to all pygame channels."""
    vol = current_volume
    for ch_num in range(NUM_CHANNELS):
        try:
            ch = pygame.mixer.Channel(ch_num)
            ch.set_volume(vol)
        except Exception:
            pass


def volume_schedule_loop():
    """Quiet hours for the BOX volume: 5% from midnight-8am ET, 20% otherwise.
    Edge-triggered — only changes volume AT the transitions, so manual changes
    during a period stick. Runs on boot too (re-applies the right level)."""
    global current_volume
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
    except Exception:
        tz = None
    last = None
    while True:
        try:
            h = (datetime.now(tz) if tz else datetime.now()).hour
            period = "night" if 0 <= h < 8 else "day"
            if period != last:
                current_volume = 0.05 if period == "night" else 0.20
                apply_volume()
                logger.info("🔉 Scheduled box volume -> %d%% (%s, %02d:00 ET)"
                            % (int(current_volume * 100), period, h))
                last = period
        except Exception as e:
            logger.warning("volume schedule error: %r" % e)
        time.sleep(60)

@webapp.route('/api/volume', methods=['GET', 'POST'])
def api_volume():
    """Get or set software volume (0-100)."""
    global current_volume
    if request.method == 'GET':
        return jsonify({"volume": int(current_volume * 100)})
    else:
        data = request.json
        ui_vol = max(0, min(100, int(data.get('volume', 100))))
        current_volume = ui_vol / 100.0
        apply_volume()
        logger.info(f"🔊 Volume: {ui_vol}%")
        return jsonify({"volume": ui_vol})


@webapp.route('/api/skip', methods=['POST'])
def api_skip():
    """Skip current track in shuffle play."""
    global shuffle_skip
    if shuffle_active:
        shuffle_skip = True
        _log_event("skipped a track")
        return jsonify({"status": "skipped"})
    return jsonify({"status": "not_shuffling"})


@webapp.route('/api/stop', methods=['POST'])
def api_stop():
    web_stop()
    return jsonify({"status": "stopped"})


@webapp.route('/api/groups')
def api_groups():
    """List all groups with their ranges."""
    groups = [{"name": name, "start": start, "end": end} for name, start, end in SLOT1_GROUPS]
    return jsonify({"groups": groups})


@webapp.route('/api/next_number')
def api_next_number():
    group = request.args.get('group', '')
    all_numbers = set()
    if os.path.isdir(slot1_folder):
        for f in os.listdir(slot1_folder):
            if f.startswith("sound_") and f.endswith(".wav"):
                n = get_file_number(f)
                if n > 0:
                    all_numbers.add(n)
    if group:
        for gname, start, end in SLOT1_GROUPS:
            if gname == group:
                group_nums = [n for n in all_numbers if start <= n <= end]
                next_num = max(group_nums) + 1 if group_nums else start
                return jsonify({"next": next_num, "group": group, "range": [start, end]})
    next_num = max(all_numbers) + 1 if all_numbers else 1
    return jsonify({"next": next_num})


@webapp.route('/api/add', methods=['POST'])
def api_add():
    data = request.json
    url = data.get('url', '')
    name = data.get('name', '')
    group = data.get('group', '')
    if not url or not name:
        return jsonify({"error": "Need url and name"}), 400
    name = name.strip().lower().replace(' ', '_').replace('-', '_')
    name = ''.join(c for c in name if c.isalnum() or c == '_')
    all_numbers = set()
    if os.path.isdir(slot1_folder):
        for f in os.listdir(slot1_folder):
            if f.startswith("sound_") and f.endswith(".wav"):
                n = get_file_number(f)
                if n > 0:
                    all_numbers.add(n)
    new_group_name = data.get('new_group', '')
    if new_group_name:
        # Create a new group on the fly
        group_info = create_new_group(new_group_name)
        number = group_info[1]  # start of new range
        group = new_group_name
    elif group:
        for gname, start, end in SLOT1_GROUPS:
            if gname == group:
                group_nums = [n for n in all_numbers if start <= n <= end]
                number = max(group_nums) + 1 if group_nums else start
                break
        else:
            number = max(all_numbers) + 1 if all_numbers else 1
    else:
        number = max(all_numbers) + 1 if all_numbers else 1
    filename = f"sound_{int(number):05d}_{name}"
    try:
        # Download directly to sound_1/ — normalizer will process in-place
        result = subprocess.run(
            ["yt-dlp", "--no-playlist", "-x", "--audio-format", "wav",
             "-o", os.path.join(slot1_folder, f"{filename}.%(ext)s"), url],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            return jsonify({"error": f"Download failed: {result.stderr[-200:]}"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Download timed out"}), 500
    # Reload sounds so new file appears in groups
    load_slot1_sounds()
    return jsonify({"status": "added", "filename": filename + ".wav", "number": number, "group": group})


@webapp.route('/api/upload/song', methods=['POST'])
def api_upload_song():
    """Upload an audio file as a song."""
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files['file']
    name = request.form.get('name', '')
    group = request.form.get('group', '')
    new_group_name = request.form.get('new_group', '')
    if not name:
        return jsonify({"error": "Need a name"}), 400
    name = name.strip().lower().replace(' ', '_').replace('-', '_')
    name = ''.join(c for c in name if c.isalnum() or c == '_')

    # Determine number
    all_numbers = set()
    if os.path.isdir(slot1_folder):
        for f in os.listdir(slot1_folder):
            if f.startswith("sound_") and f.endswith(".wav"):
                n = get_file_number(f)
                if n > 0:
                    all_numbers.add(n)

    if new_group_name:
        group_info = create_new_group(new_group_name)
        number = group_info[1]
        group = new_group_name
    elif group:
        for gname, start, end in SLOT1_GROUPS:
            if gname == group:
                group_nums = [n for n in all_numbers if start <= n <= end]
                number = max(group_nums) + 1 if group_nums else start
                break
        else:
            number = max(all_numbers) + 1 if all_numbers else 1
    else:
        number = max(all_numbers) + 1 if all_numbers else 1

    filename = f"sound_{int(number):05d}_{name}"
    temp_path = os.path.join(slot1_folder, f"{filename}_temp")
    final_path = os.path.join(slot1_folder, f"{filename}.wav")

    try:
        file.save(temp_path)
        # Convert to wav 44100Hz
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", temp_path, "-ar", "44100", "-ac", "2", final_path],
            capture_output=True, text=True, timeout=120
        )
        os.remove(temp_path)
        if result.returncode != 0:
            return jsonify({"error": "Failed to convert audio"}), 500
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return jsonify({"error": str(e)}), 500

    load_slot1_sounds()
    return jsonify({"status": "added", "filename": filename + ".wav", "number": number, "group": group})


@webapp.route('/api/upload/sfx', methods=['POST'])
def api_upload_sfx():
    """Upload an audio file as an SFX."""
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files['file']
    name = request.form.get('name', '')
    if not name:
        return jsonify({"error": "Need a name"}), 400
    name = name.strip().lower().replace(' ', '_').replace('-', '_')
    name = ''.join(c for c in name if c.isalnum() or c == '_')

    filename = f"sfx_{name}"
    os.makedirs(SFX_DIR, exist_ok=True)
    temp_path = os.path.join(SFX_DIR, f"{filename}_temp")
    final_path = os.path.join(SFX_DIR, f"{filename}.wav")

    try:
        file.save(temp_path)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", temp_path, "-ar", "44100", "-ac", "2", final_path],
            capture_output=True, text=True, timeout=120
        )
        os.remove(temp_path)
        if result.returncode != 0:
            return jsonify({"error": "Failed to convert audio"}), 500
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "added", "filename": filename + ".wav"})


@webapp.route('/api/usage')
def api_usage():
    """Get usage stats."""
    import json
    if os.path.exists(USAGE_FILE):
        with open(USAGE_FILE) as f:
            return jsonify(json.load(f))
    return jsonify({})


@webapp.route('/api/search')
def api_search():
    """Search across all songs, SFX, and DCC clips."""
    query = request.args.get('q', '').lower().strip()
    if not query:
        return jsonify({"results": []})

    results = []

    # Search songs
    if os.path.isdir(slot1_folder):
        for f in os.listdir(slot1_folder):
            if f.endswith(".wav") and ".tmp." not in f and query in f.lower().replace('_', ' '):
                results.append({"filename": f, "display": get_display_name(f), "type": "song",
                                "number": get_file_number(f)})

    # Search SFX
    if os.path.isdir(SFX_DIR):
        for f in os.listdir(SFX_DIR):
            if f.endswith(".wav") and query in f.lower().replace('_', ' '):
                name = f.replace('.wav', '').replace('sfx_', '').replace('_', ' ').title()
                results.append({"filename": f, "display": name, "type": "sfx"})

    # Search DCC
    if os.path.isdir(slot8_folder):
        for d in os.listdir(slot8_folder):
            dirpath = os.path.join(slot8_folder, d)
            if os.path.isdir(dirpath):
                for f in os.listdir(dirpath):
                    if f.endswith(".wav") and query in f.lower().replace('_', ' '):
                        name = f.replace('.wav', '').replace('_', ' ')
                        results.append({"filename": f, "display": name, "type": "dcc", "category": d})

    return jsonify({"results": results, "query": query})


def _current_sound():
    """Most-recently-triggered audible sound (song on web_channel OR sfx) for
    event-driven browser audio. Returns dict {start,path,name,paused} or None."""
    cands = []
    try:
        if web_channel and (web_channel.get_busy() or web_paused) and web_now_playing_path:
            cands.append((web_play_start, web_now_playing_path, web_now_playing, bool(web_paused)))
    except Exception:
        pass
    try:
        if sfx_channel and sfx_channel.get_busy() and sfx_now_playing_path:
            cands.append((sfx_play_start, sfx_now_playing_path, sfx_now_playing_name, False))
    except Exception:
        pass
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])   # latest trigger wins
    s, p, n, pz = cands[-1]
    return {"start": s, "path": p, "name": n, "paused": pz}


@webapp.route('/api/nowplaying')
def api_nowplaying():
    """Event-driven browser audio: what's playing + a per-trigger token (song or sfx)."""
    cur = _current_sound()
    if not cur:
        return jsonify({"playing": False, "paused": False, "token": "", "name": ""})
    return jsonify({"playing": True, "paused": cur["paused"],
                    "token": str(cur["start"]), "name": cur["name"]})


@webapp.route('/api/nowplaying.wav')
def api_nowplaying_wav():
    """Serve the current sound (song or sfx) from the start (range-enabled)."""
    from flask import send_file as _send_file
    cur = _current_sound()
    if not cur or not os.path.exists(cur["path"]):
        return ("no track", 404)
    return _send_file(cur["path"], mimetype="audio/wav", conditional=True)


from collections import deque as _deque
_CHAT = _deque(maxlen=5000)
_CHAT_SEQ = 0
_CHAT_LOCK = threading.Lock()
_ACTIVITY = _deque(maxlen=5000)
_ACTIVITY_SEQ = 0
_ACTIVITY_LOCK = threading.Lock()

# --- persistence: chat + activity survive reboots; messages expire after 3 days ---
_CHAT_FILE = os.path.join(os.path.dirname(SOUND_DIR), "chat_store.json")
_ACTIVITY_FILE = os.path.join(os.path.dirname(SOUND_DIR), "activity_store.json")
_MSG_TTL = 3 * 86400


def _prune_msgs(seq):
    now = time.time()
    return [m for m in seq if now - m.get("ts", 0) <= _MSG_TTL]


def _store_load(path, dq, lock):
    try:
        with open(path) as f:
            data = _prune_msgs(json.load(f))
        with lock:
            dq.clear()
            dq.extend(data)
        return max([m.get("id", 0) for m in data], default=0)
    except Exception:
        return 0


def _store_save(path, dq, lock):
    try:
        with lock:
            data = _prune_msgs(list(dq))
            dq.clear()
            dq.extend(data)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _persist_loop():
    while True:
        time.sleep(60)
        _store_save(_CHAT_FILE, _CHAT, _CHAT_LOCK)
        _store_save(_ACTIVITY_FILE, _ACTIVITY, _ACTIVITY_LOCK)



def _log_system(action, detail=""):
    """Append a system event (e.g. reboot) to the activity log."""
    global _ACTIVITY_SEQ
    try:
        with _ACTIVITY_LOCK:
            _ACTIVITY_SEQ += 1
            _ACTIVITY.append({"id": _ACTIVITY_SEQ, "ts": time.time(),
                              "who": "\U0001F916 system", "action": action, "detail": detail})
        _store_save(_ACTIVITY_FILE, _ACTIVITY, _ACTIVITY_LOCK)
    except Exception:
        pass


# load persisted history on startup (anything older than 3 days is dropped)
_CHAT_SEQ = _store_load(_CHAT_FILE, _CHAT, _CHAT_LOCK)
_ACTIVITY_SEQ = _store_load(_ACTIVITY_FILE, _ACTIVITY, _ACTIVITY_LOCK)


def _log_event(action, detail=""):
    """Record a 'who did what' event for the browser activity log."""
    global _ACTIVITY_SEQ
    try:
        who = _trigger_name() or "someone"
        with _ACTIVITY_LOCK:
            _ACTIVITY_SEQ += 1
            _ACTIVITY.append({"id": _ACTIVITY_SEQ, "ts": time.time(),
                              "who": who, "action": action, "detail": detail})
    except Exception:
        pass


@webapp.route('/api/chat', methods=['GET', 'POST'])
def api_chat():
    """Tiny room chat. POST {text} to send; GET ?since=<id> to fetch new messages."""
    global _CHAT_SEQ
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        text = (data.get('text') or '').strip()[:280]
        if not text:
            return jsonify({"error": "empty"}), 400
        name = _trigger_name() or "guest"
        with _CHAT_LOCK:
            _CHAT_SEQ += 1
            msg = {"id": _CHAT_SEQ, "name": name, "text": text, "ts": time.time()}
            _CHAT.append(msg)
        _store_save(_CHAT_FILE, _CHAT, _CHAT_LOCK)
        return jsonify({"status": "ok", "id": msg["id"]})
    try:
        since = int(request.args.get('since', 0))
    except (TypeError, ValueError):
        since = 0
    with _CHAT_LOCK:
        msgs = [m for m in _CHAT if m["id"] > since]
    return jsonify({"messages": msgs})


@webapp.after_request
def _no_cache_api(resp):
    try:
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            resp.headers["Pragma"] = "no-cache"
    except Exception:
        pass
    return resp


@webapp.route('/api/activity')
def api_activity():
    try:
        since = int(request.args.get('since', 0))
    except (TypeError, ValueError):
        since = 0
    with _ACTIVITY_LOCK:
        evs = [e for e in _ACTIVITY if e["id"] > since]
    return jsonify({"events": evs})


def _resolve_sound_path(filename):
    """Locate an SFX/DCC clip by basename across sfx/ and sound_8/ (for charts playback)."""
    if not filename.endswith(".wav") or "/" in filename or ".." in filename:
        return None
    for base in (SFX_DIR, slot8_folder):
        if not os.path.isdir(base):
            continue
        for d in os.listdir(base):
            fp = os.path.join(base, d, filename)
            if os.path.isfile(fp):
                return fp
        fp = os.path.join(base, filename)
        if os.path.isfile(fp):
            return fp
    return None


@webapp.route('/api/limits', methods=['GET', 'POST'])
def api_limits():
    if request.method == 'POST':
        if not _session.get("admin"):
            return jsonify({"error": "Admin only"}), 403
        data = request.get_json(silent=True) or {}
        try:
            _LIMITS["songs_per_person"] = max(1, min(10, int(data.get("songs_per_person", _LIMITS["songs_per_person"]))))
            _LIMITS["sfx_per_person"] = max(1, min(20, int(data.get("sfx_per_person", _LIMITS["sfx_per_person"]))))
        except (TypeError, ValueError):
            return jsonify({"error": "bad values"}), 400
        _save_limits()
    return jsonify(_LIMITS)


@webapp.route('/api/top')
def api_top():
    """Top 50 songs + top 50 sounds by all-time play count (from usage.json)."""
    try:
        with open(USAGE_FILE) as f:
            usage = json.load(f)
    except Exception:
        usage = {}
    songs, sounds = [], []
    for fn, info in usage.items():
        cnt = info.get("count", 0)
        src = info.get("source", "")
        if src == "web":
            if os.path.isfile(os.path.join(slot1_folder, fn)):
                songs.append((cnt, fn))
        elif src == "sfx":
            if _resolve_sound_path(fn):
                sounds.append((cnt, fn))
    songs.sort(reverse=True)
    sounds.sort(reverse=True)
    fmt = lambda lst: [{"filename": fn, "display": get_display_name(fn), "count": c} for c, fn in lst[:50]]
    return jsonify({"songs": fmt(songs), "sounds": fmt(sounds)})


@webapp.route('/api/play/top', methods=['POST'])
def api_play_top():
    """Play a song or sound chosen from the Top charts (by filename)."""
    data = request.get_json(silent=True) or {}
    fn = data.get("filename", "")
    kind = data.get("kind", "")
    if not fn.endswith(".wav") or "/" in fn or ".." in fn:
        return jsonify({"error": "bad filename"}), 400
    if kind == "song":
        fp = os.path.join(slot1_folder, fn)
        if not os.path.isfile(fp):
            return jsonify({"error": "not found"}), 404
        web_play_file(fp)
        return jsonify({"status": "playing"})
    fp = _resolve_sound_path(fn)
    if not fp:
        return jsonify({"error": "not found"}), 404
    sfx_play_file(fp)
    return jsonify({"status": "playing"})


_METRICS = _deque(maxlen=400)
_METRICS_LOCK = threading.Lock()


@webapp.route('/api/metric', methods=['POST', 'GET'])
def api_metric():
    """Click->audible latency. POST {ms, sync} records; GET (admin) returns stats."""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        try:
            ms = float(data.get('ms', 0))
        except (TypeError, ValueError):
            ms = 0
        if 0 < ms < 60000:
            with _METRICS_LOCK:
                _METRICS.append({"ms": ms, "sync": bool(data.get('sync')),
                                 "who": _trigger_name() or "?", "ts": time.time()})
        return jsonify({"ok": True})
    if not _session.get("admin"):
        return jsonify({"error": "admin only"}), 403
    with _METRICS_LOCK:
        snap = list(_METRICS)
    def stats(rows):
        v = sorted(r["ms"] for r in rows)
        n = len(v)
        pc = lambda q: round(v[min(n - 1, int(q * n))]) if n else 0
        return {"count": n, "avg": round(sum(v) / n) if n else 0,
                "p50": pc(.5), "p90": pc(.9), "max": round(v[-1]) if n else 0,
                "last": round(rows[-1]["ms"]) if rows else 0}
    return jsonify({"snappy": stats([r for r in snap if not r["sync"]]),
                    "sync": stats([r for r in snap if r["sync"]])})


@webapp.route('/api/time')
def api_time():
    return jsonify({"t": time.time()})


@webapp.route('/api/active')
def api_active():
    """List of currently-playing sounds (songs + sfx) for layered browser audio."""
    return jsonify({"active": _active_list()})


@webapp.route('/api/active/<int:token>.wav')
def api_active_wav(token):
    from flask import send_file as _send_file
    with _ACTIVE_LOCK:
        e = _ACTIVE.get(token)
        path = e["path"] if e else None
    if not path or not os.path.exists(path):
        return ("gone", 404)
    return _send_file(path, mimetype="audio/wav", conditional=True)


_AUDIO_CACHE_DIR = os.path.join(os.path.dirname(SOUND_DIR), "cache_audio")
_TRANSCODE_LOCKS = {}
_TRANSCODE_GLOBAL_LOCK = threading.Lock()


def _sound_key(src_path):
    """Stable content id for a sound file (path+mtime+size) — shared by the MP3 cache
    and exposed to browsers so they can cache short clips for instant replay."""
    import hashlib
    try:
        st = os.stat(src_path)
    except OSError:
        return ""
    return hashlib.md5(("%s|%d|%d" % (src_path, int(st.st_mtime), st.st_size)).encode()).hexdigest()


def _cached_mp3(src_path):
    """Return a cached 128k MP3 of src_path, transcoding once (keyed by path+mtime+size)."""
    key = _sound_key(src_path)
    if not key:
        return None
    out = os.path.join(_AUDIO_CACHE_DIR, key + ".mp3")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    with _TRANSCODE_GLOBAL_LOCK:                 # one lock per key -> no double-transcode
        lk = _TRANSCODE_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _TRANSCODE_LOCKS[key] = lk
    with lk:
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
        try:
            os.makedirs(_AUDIO_CACHE_DIR, exist_ok=True)
            tmp = out + ".tmp"
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src_path,
                                "-ac", "2", "-ar", "44100", "-b:a", "128k", "-f", "mp3", tmp],
                               capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.exists(tmp):
                os.replace(tmp, out)
                return out
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return None


@webapp.route('/api/active/<int:token>.mp3')
def api_active_mp3(token):
    """Browser audio as compressed MP3 (cached). ~11x less bandwidth than the wav."""
    from flask import send_file as _send_file
    with _ACTIVE_LOCK:
        e = _ACTIVE.get(token)
        path = e["path"] if e else None
    if not path or not os.path.exists(path):
        return ("gone", 404)
    mp3 = _cached_mp3(path)
    if mp3:
        return _send_file(mp3, mimetype="audio/mpeg", conditional=True)
    return _send_file(path, mimetype="audio/wav", conditional=True)   # fallback


@webapp.route('/api/active/<int:token>/stop', methods=['POST'])
def api_active_stop(token):
    """Admin: kill one specific active sound by token."""
    if not _session.get("admin"):
        return jsonify({"error": "Admin only"}), 403
    with _ACTIVE_LOCK:
        e = _ACTIVE.pop(token, None)
    if not e:
        return jsonify({"status": "gone"})
    try:
        e["ch"].stop()
    except Exception:
        pass
    try:
        _log_event("killed", e.get("name", ""))
    except Exception:
        pass
    return jsonify({"status": "killed"})


@webapp.route('/api/status')
def api_status():
    playing = False
    now_playing = ""
    elapsed = 0
    duration = 0
    paused = web_paused
    if web_channel and (web_channel.get_busy() or web_paused):
        playing = True
        now_playing = web_now_playing
        duration = round(web_play_duration, 1)
        if web_paused:
            elapsed = round(web_pause_elapsed, 1)
        else:
            elapsed = round(time.time() - web_play_start, 1)
    return jsonify({
        "connected": True,
        "playing": playing,
        "paused": paused,
        "shuffling": shuffle_active,
        "now_playing": now_playing,
        "elapsed": elapsed,
        "duration": duration,
    })


def start_web_server():
    """Run Flask in a background thread."""
    webapp.run(host='0.0.0.0', port=WEB_PORT, debug=False, use_reloader=False, threaded=True)


# =============================================================================
# MAIN
# =============================================================================

def main():
    global web_channel

    logger.info("=" * 50)
    logger.info("RASPBERRY PI SOUNDBOARD")
    logger.info("=" * 50)

    # Force audio output to USB DAC (card 3)
    os.environ['SDL_AUDIODRIVER'] = 'alsa'
    os.environ['AUDIODEV'] = 'hw:3,0'
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
    pygame.mixer.set_num_channels(NUM_CHANNELS)
    apply_volume()  # boot default (overridden immediately by the schedule)
    threading.Thread(target=volume_schedule_loop, daemon=True).start()
    threading.Thread(target=_persist_loop, daemon=True).start()
    try:
        _sysup = float(open("/proc/uptime").read().split()[0])
    except Exception:
        _sysup = 9999
    _log_system("rebooted \u2014 all playback was stopped" if _sysup < 120 else "restarted (service)")
    web_channel = pygame.mixer.Channel(WEB_CHANNEL)
    sfx_channel = pygame.mixer.Channel(SFX_CHANNEL)

    ensure_sound_dir()
    load_existing_sounds()
    setup_gpio()

    monitor = threading.Thread(target=slot1_playback_monitor, daemon=True)
    monitor.start()

    # Start web server in background thread
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()
    logger.info(f"🌐 Web UI running on port {WEB_PORT}")

    logger.info("Ready!")
    logger.info("  Hold RECORD + press 1 = cycle song group")
    logger.info("  Hold RECORD + press 8 = cycle DCC speaker")
    logger.info("  Press 1 = play current song (within 10s = next in group)")
    logger.info("  Press 8 = random DCC quote from current speaker")
    logger.info(f"  Web UI: http://soundboard:{WEB_PORT}")
    logger.info("  Ctrl+C to exit")
    logger.info("=" * 50)

    try:
        pause()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        if record_led:
            record_led.off()
        pygame.quit()


if __name__ == "__main__":
    main()
