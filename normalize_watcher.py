#!/usr/bin/env python3
"""
Sound normalizer — in-place mode.
Normalizes wav files directly without creating copies.
Watches sound_1/ for files without .normalized markers.
"""

import os
import json
import subprocess
import time
from pathlib import Path

SOUND_DIR = "/home/pi/soundboard/sounds"
PROCESSED_MARKER = ".normalized"
MAX_RETRIES = 3
TIMEOUT_PER_MB = 20
MIN_TIMEOUT = 120

# Loudnorm targets (tuned for Creative Pebble Pro)
TARGET_I = -8
TARGET_TP = -0.5
TARGET_LRA = 7
OUTPUT_SAMPLE_RATE = 44100

SLOT_FOLDERS = ["sound_1"]
retry_counts = {}


def is_normalized(wav_path):
    marker = wav_path + PROCESSED_MARKER
    if not os.path.exists(marker):
        return False
    if os.path.getmtime(wav_path) > os.path.getmtime(marker):
        return False
    return True


def mark_normalized(wav_path):
    Path(wav_path + PROCESSED_MARKER).touch()
    retry_counts.pop(wav_path, None)


def mark_failed(wav_path):
    Path(wav_path + ".normalize_failed").touch()
    print(f"⛔ Giving up on {os.path.basename(wav_path)} after {MAX_RETRIES} attempts")


def get_timeout(wav_path):
    size_mb = os.path.getsize(wav_path) / (1024 * 1024)
    return max(MIN_TIMEOUT, int(size_mb * TIMEOUT_PER_MB))


def normalize_file(wav_path):
    """Normalize a file in-place using two-pass loudnorm."""
    count = retry_counts.get(wav_path, 0)
    if count >= MAX_RETRIES:
        mark_failed(wav_path)
        return False

    retry_counts[wav_path] = count + 1
    timeout = get_timeout(wav_path)
    basename = os.path.basename(wav_path)

    print(f"Normalizing: {basename} (attempt {count + 1}/{MAX_RETRIES})")

    temp_path = wav_path + ".tmp.wav"

    try:
        # Pass 1: measure
        loudnorm_filter = f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json"
        result = subprocess.run([
            "ffmpeg", "-i", wav_path,
            "-af", loudnorm_filter,
            "-f", "null", "/dev/null"
        ], capture_output=True, text=True, timeout=timeout)

        if result.returncode != 0:
            print(f"❌ Pass 1 failed: {basename}")
            return False

        stderr = result.stderr
        json_start = stderr.rfind("{")
        json_end = stderr.rfind("}") + 1
        if json_start == -1 or json_end == 0:
            print(f"❌ Could not parse loudnorm stats: {basename}")
            return False
        stats = json.loads(stderr[json_start:json_end])

        print(f"  Measured: I={stats.get('input_i')} LUFS, TP={stats.get('input_tp')} dB")

        # Pass 2: normalize to temp file
        loudnorm_filter = (
            f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
            f":measured_I={stats['input_i']}"
            f":measured_TP={stats['input_tp']}"
            f":measured_LRA={stats['input_lra']}"
            f":measured_thresh={stats['input_thresh']}"
            f":offset={stats['target_offset']}"
            f":linear=true"
        )

        result = subprocess.run([
            "ffmpeg", "-y", "-i", wav_path,
            "-af", loudnorm_filter,
            "-ar", str(OUTPUT_SAMPLE_RATE),
            temp_path
        ], capture_output=True, text=True, timeout=timeout)

        if result.returncode == 0 and os.path.exists(temp_path):
            os.replace(temp_path, wav_path)
            mark_normalized(wav_path)
            print(f"✅ Normalized: {basename}")
            return True
        else:
            print(f"❌ Pass 2 failed: {basename}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False

    except subprocess.TimeoutExpired:
        print(f"❌ Timed out: {basename}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False


def scan_and_normalize():
    files_to_normalize = []

    for folder_name in SLOT_FOLDERS:
        folder_path = os.path.join(SOUND_DIR, folder_name)
        if not os.path.isdir(folder_path):
            continue
        for filename in os.listdir(folder_path):
            if filename.endswith(".wav") and ".tmp." not in filename:
                wav_path = os.path.join(folder_path, filename)
                if not is_normalized(wav_path):
                    failed_marker = wav_path + ".normalize_failed"
                    if os.path.exists(failed_marker):
                        if os.path.getmtime(wav_path) > os.path.getmtime(failed_marker):
                            os.remove(failed_marker)
                            retry_counts.pop(wav_path, None)
                        else:
                            continue
                    files_to_normalize.append(wav_path)

    if not files_to_normalize:
        return

    print(f"Found {len(files_to_normalize)} files to normalize")

    success_count = 0
    for wav_path in files_to_normalize:
        time.sleep(1)
        if normalize_file(wav_path):
            success_count += 1

    if success_count > 0:
        print(f"✅ Normalized {success_count} files")


def cleanup_temp_files():
    for folder_name in SLOT_FOLDERS:
        folder_path = os.path.join(SOUND_DIR, folder_name)
        if not os.path.isdir(folder_path):
            continue
        for filename in os.listdir(folder_path):
            if ".tmp." in filename:
                try:
                    os.remove(os.path.join(folder_path, filename))
                    print(f"🧹 Cleaned: {filename}")
                except Exception:
                    pass


def main():
    print("Sound normalizer (in-place mode)")
    print(f"Targets: I={TARGET_I} LUFS, TP={TARGET_TP} dB, LRA={TARGET_LRA}")
    print("Checking every 10 seconds\n")

    cleanup_temp_files()

    while True:
        try:
            scan_and_normalize()
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(10)


if __name__ == "__main__":
    main()
