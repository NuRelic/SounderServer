#!/usr/bin/env python3
"""Play all slot 1 sounds sequentially."""
import os, sys, subprocess, random

folder = "/home/pi/soundboard/sounds/sound_1"
wavs = sorted(f for f in os.listdir(folder) if f.endswith(".wav") and ".tmp." not in f)

random.shuffle(wavs)
print(f"Shuffling {len(wavs)} sounds from slot 1\n")
for i, f in enumerate(wavs, 1):
    path = os.path.join(folder, f)
    print(f"[{i}/{len(wavs)}] {f}")
    try:
        subprocess.run(["aplay", path], check=True)
    except KeyboardInterrupt:
        print("\nSkipping...")
        try:
            input("Press Enter for next, Ctrl+C again to quit")
        except KeyboardInterrupt:
            print("\nDone.")
            sys.exit(0)
print("\nAll done!")
