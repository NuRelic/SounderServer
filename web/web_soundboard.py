#!/usr/bin/env python3
"""
Soundboard Web Controller — Pi version
Runs directly on the Pi, uses local file paths and aplay.
"""

import os
import subprocess
import json
import random
from flask import Flask, render_template, jsonify, request

app = Flask(__name__, template_folder='/home/pi/soundboard/web/templates')

SOUND_DIR = "/home/pi/soundboard/sounds"

# 5-digit GGSSS scheme: GG = group ID, SSS = song position
# Each group has 999 slots, groups can never collide
SLOT1_GROUPS = [
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
]

# Current playback processes
current_playback = None
shuffle_process = None


def kill_aplay():
    """Kill any running aplay and shuffle processes."""
    global current_playback, shuffle_process
    # Kill the shuffle bash process first (stops the loop)
    if shuffle_process:
        try:
            shuffle_process.kill()
            shuffle_process.wait(timeout=2)
        except Exception:
            pass
        shuffle_process = None
    # Kill any aplay
    try:
        subprocess.run(["killall", "aplay"], capture_output=True, timeout=3)
    except Exception:
        pass
    if current_playback:
        try:
            current_playback.kill()
        except Exception:
            pass
        current_playback = None


def restart_soundboard_after(proc):
    """Wait for a process to finish, then restart soundboard service."""
    try:
        proc.wait()
    except Exception:
        pass
    # Only restart if nothing else is playing
    try:
        result = subprocess.run(["pgrep", "aplay"], capture_output=True, timeout=3)
        if result.returncode != 0:  # no aplay running
            subprocess.run(["sudo", "systemctl", "restart", "soundboard.service"],
                           capture_output=True, timeout=5)
    except Exception:
        pass


def play_file(filepath):
    """Play a wav file, killing any current playback first."""
    global current_playback
    kill_aplay()
    # Stop soundboard service to free audio device
    subprocess.run(["sudo", "systemctl", "stop", "soundboard.service"],
                   capture_output=True, timeout=5)
    import time
    time.sleep(0.3)
    current_playback = subprocess.Popen(["aplay", filepath])
    # Auto-restart soundboard when done
    import threading
    threading.Thread(target=restart_soundboard_after, args=(current_playback,), daemon=True).start()


def get_file_number(filename):
    try:
        return int(filename.split('_')[1])
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


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/songs')
def api_songs():
    slot1_dir = os.path.join(SOUND_DIR, "sound_1")
    all_files = sorted([
        f for f in os.listdir(slot1_dir)
        if f.endswith(".wav") and ".tmp." not in f and os.path.isfile(os.path.join(slot1_dir, f))
    ])

    groups = []
    for group_name, start, end in SLOT1_GROUPS:
        songs = []
        for f in all_files:
            num = get_file_number(f)
            if start <= num <= end:
                songs.append({
                    "filename": f,
                    "number": num,
                    "display": get_display_name(f),
                })
        if songs:
            groups.append({"name": group_name, "songs": songs})

    # Add "All" group with every song
    all_songs = [{"filename": f, "number": get_file_number(f), "display": get_display_name(f)}
                 for f in all_files]
    if all_songs:
        groups.append({"name": "All", "songs": all_songs})

    return jsonify({"groups": groups})


@app.route('/api/dcc/categories')
def api_dcc_categories():
    slot8_dir = os.path.join(SOUND_DIR, "sound_8")
    categories = []
    total = 0
    for d in sorted(os.listdir(slot8_dir)):
        dirpath = os.path.join(slot8_dir, d)
        if os.path.isdir(dirpath):
            count = len([f for f in os.listdir(dirpath) if f.endswith(".wav")])
            if count > 0:
                categories.append({"name": d, "count": count})
                total += count
    categories.append({"name": "all", "count": total})
    return jsonify({"categories": categories})


@app.route('/api/dcc/clips/<category>')
def api_dcc_clips(category):
    slot8_dir = os.path.join(SOUND_DIR, "sound_8")
    clips = []
    if category == "all":
        for d in os.listdir(slot8_dir):
            dirpath = os.path.join(slot8_dir, d)
            if os.path.isdir(dirpath):
                clips.extend([f for f in os.listdir(dirpath) if f.endswith(".wav")])
    else:
        dirpath = os.path.join(slot8_dir, category)
        if os.path.isdir(dirpath):
            clips = [f for f in os.listdir(dirpath) if f.endswith(".wav")]
    return jsonify({"clips": sorted(clips)})


@app.route('/api/play/sound1', methods=['POST'])
def play_sound1():
    data = request.json
    filename = data.get('filename', '')
    if not filename.endswith('.wav') or '/' in filename or '..' in filename:
        return jsonify({"error": "Invalid filename"}), 400
    filepath = os.path.join(SOUND_DIR, "sound_1", filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    play_file(filepath)
    return jsonify({"status": "playing", "filename": filename})


@app.route('/api/play/slot/<int:slot>', methods=['POST'])
def play_slot(slot):
    if slot < 2 or slot > 7:
        return jsonify({"error": "Invalid slot"}), 400
    filepath = os.path.join(SOUND_DIR, f"sound_{slot}.wav")
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    play_file(filepath)
    return jsonify({"status": "playing", "slot": slot})


@app.route('/api/play/dcc', methods=['POST'])
def play_dcc():
    data = request.json
    category = data.get('category', 'all')
    slot8_dir = os.path.join(SOUND_DIR, "sound_8")

    clips = []
    if category == "all":
        for d in os.listdir(slot8_dir):
            dirpath = os.path.join(slot8_dir, d)
            if os.path.isdir(dirpath):
                clips.extend([
                    os.path.join(dirpath, f)
                    for f in os.listdir(dirpath) if f.endswith(".wav")
                ])
    else:
        dirpath = os.path.join(slot8_dir, category)
        if os.path.isdir(dirpath):
            clips = [
                os.path.join(dirpath, f)
                for f in os.listdir(dirpath) if f.endswith(".wav")
            ]

    if not clips:
        return jsonify({"error": "No clips found"}), 404

    pick = random.choice(clips)
    play_file(pick)
    return jsonify({"status": "playing", "clip": os.path.basename(pick)})


@app.route('/api/play/dcc/clip', methods=['POST'])
def play_dcc_clip():
    data = request.json
    category = data.get('category', '')
    filename = data.get('filename', '')

    if not filename.endswith('.wav') or '..' in filename:
        return jsonify({"error": "Invalid filename"}), 400

    slot8_dir = os.path.join(SOUND_DIR, "sound_8")

    if category == "all":
        # Search all subdirs
        for d in os.listdir(slot8_dir):
            dirpath = os.path.join(slot8_dir, d)
            filepath = os.path.join(dirpath, filename)
            if os.path.isfile(filepath):
                play_file(filepath)
                return jsonify({"status": "playing", "filename": filename})
        return jsonify({"error": "File not found"}), 404
    else:
        filepath = os.path.join(slot8_dir, category, filename)
        if not os.path.exists(filepath):
            return jsonify({"error": "File not found"}), 404
        play_file(filepath)
        return jsonify({"status": "playing", "filename": filename})


@app.route('/api/play/shuffle_group', methods=['POST'])
def play_shuffle_group():
    """Play all songs in a group in random order."""
    data = request.json
    group_name = data.get('group', '')

    slot1_dir = os.path.join(SOUND_DIR, "sound_1")
    all_files = sorted([
        f for f in os.listdir(slot1_dir)
        if f.endswith(".wav") and ".tmp." not in f and os.path.isfile(os.path.join(slot1_dir, f))
    ])

    # Find group range
    songs = []
    for gname, start, end in SLOT1_GROUPS:
        if gname == group_name:
            songs = [f for f in all_files if start <= get_file_number(f) <= end]
            break

    if not songs:
        return jsonify({"error": "No songs in group"}), 404

    random.shuffle(songs)

    # Write playlist to temp file, launch background player
    playlist_path = "/tmp/soundboard_playlist.txt"
    with open(playlist_path, 'w') as pf:
        for s in songs:
            pf.write(os.path.join(slot1_dir, s) + '\n')

    kill_aplay()
    subprocess.run(["sudo", "systemctl", "stop", "soundboard.service"],
                   capture_output=True, timeout=5)
    import time
    time.sleep(0.3)

    # Launch background script that plays each file
    global shuffle_process
    shuffle_process = subprocess.Popen([
        "bash", "-c",
        f"while IFS= read -r f; do aplay \"$f\"; done < {playlist_path}"
    ])
    # Auto-restart soundboard when shuffle finishes
    import threading
    threading.Thread(target=restart_soundboard_after, args=(shuffle_process,), daemon=True).start()

    return jsonify({"status": "playing", "group": group_name, "count": len(songs)})


@app.route('/api/stop', methods=['POST'])
def stop_playback():
    kill_aplay()
    subprocess.run(["sudo", "systemctl", "restart", "soundboard.service"],
                   capture_output=True, timeout=5)
    return jsonify({"status": "stopped"})


@app.route('/api/next_number')
def api_next_number():
    """Get the next available song number, optionally for a specific group."""
    group = request.args.get('group', '')
    originals_dir = os.path.join(SOUND_DIR, "sound_1", "originals")
    slot1_dir = os.path.join(SOUND_DIR, "sound_1")

    # Collect all numbers from both dirs
    all_numbers = set()
    for d in [originals_dir, slot1_dir]:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.startswith("sound_") and f.endswith(".wav"):
                    n = get_file_number(f)
                    if n > 0:
                        all_numbers.add(n)

    if group:
        # Find the range for this group and suggest next number within/after it
        for gname, start, end in SLOT1_GROUPS:
            if gname == group:
                # Find first gap or use end+1
                # But to keep it simple, use max existing in that range + 1
                group_nums = [n for n in all_numbers if start <= n <= end]
                if group_nums:
                    next_num = max(group_nums) + 1
                else:
                    next_num = start
                return jsonify({"next": next_num, "group": group, "range": [start, end]})

    # Global next
    next_num = max(all_numbers) + 1 if all_numbers else 1
    return jsonify({"next": next_num})


@app.route('/api/add', methods=['POST'])
def add_song():
    data = request.json
    url = data.get('url', '')
    name = data.get('name', '')
    group = data.get('group', '')

    if not url or not name:
        return jsonify({"error": "Need url and name"}), 400

    # Clean up name
    name = name.strip().lower().replace(' ', '_').replace('-', '_')
    # Remove any non-alphanumeric/underscore chars
    name = ''.join(c for c in name if c.isalnum() or c == '_')

    originals_dir = os.path.join(SOUND_DIR, "sound_1", "originals")
    slot1_dir = os.path.join(SOUND_DIR, "sound_1")
    os.makedirs(originals_dir, exist_ok=True)

    # Auto-assign number based on group
    all_numbers = set()
    for d in [originals_dir, slot1_dir]:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.startswith("sound_") and f.endswith(".wav"):
                    n = get_file_number(f)
                    if n > 0:
                        all_numbers.add(n)

    if group:
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
        result = subprocess.run(
            ["yt-dlp", "--no-playlist", "-x", "--audio-format", "wav",
             "-o", os.path.join(originals_dir, f"{filename}.%(ext)s"), url],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            return jsonify({"error": f"Download failed: {result.stderr[-200:]}"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Download timed out"}), 500

    return jsonify({"status": "added", "filename": filename + ".wav", "number": number, "group": group})


@app.route('/api/status')
def api_status():
    try:
        result = subprocess.run(["pgrep", "aplay"], capture_output=True, timeout=3)
        playing = result.returncode == 0
    except Exception:
        playing = False
    return jsonify({"connected": True, "playing": playing})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
