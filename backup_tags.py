#!/usr/bin/env python3
"""Pull prod's tag store into the repo so it survives losing the box.

`data/` is excluded from the deploy rsync, so data/tags.json lives in exactly
one place: the VPS disk. It holds hours of hand curation — labels, merges,
parenting, per-clip assignments — that nothing else can reconstruct. The
on-box rolling snapshots in data/tags-history/ protect against a bad edit;
they do not protect against the disk.

Prod stays authoritative. The copy in backups/ is a backup, never a source —
never push it back without knowing it is newer.

    python3 backup_tags.py            # pull + show what changed
    python3 backup_tags.py --commit   # pull + git commit if it changed
"""
import argparse
import json
import os
import subprocess
import sys

HOST = os.environ.get("SS_HOST", "sound@149.28.114.237")
REMOTE = "/home/sound/sound-server/data/tags.json"
REPO = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(REPO, "backups", "tags.json")


def summarise(path):
    try:
        d = json.load(open(path))
    except (OSError, ValueError):
        return None
    tags = d.get("tags") or {}
    tops = [s for s, r in tags.items() if not r.get("parent")]
    return {"tags": len(tags), "top": len(tops), "assigned": len(d.get("assign") or {})}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true", help="git commit if it changed")
    a = ap.parse_args(argv)

    before = summarise(DEST)
    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    tmp = DEST + ".tmp"
    r = subprocess.run(["scp", "-q", f"{HOST}:{REMOTE}", tmp])
    if r.returncode != 0:
        print("scp failed — is the box reachable?", file=sys.stderr)
        return 1

    after = summarise(tmp)
    if after is None:
        os.remove(tmp)
        print("pulled file is not valid JSON — refusing to overwrite the backup",
              file=sys.stderr)
        return 1
    os.replace(tmp, DEST)

    print(f"prod: {after['tags']} tags, {after['top']} top-level, {after['assigned']} assigned")
    if before:
        d = {k: after[k] - before[k] for k in after}
        if any(d.values()):
            print("changed since last backup: " +
                  ", ".join(f"{k} {v:+d}" for k, v in d.items() if v))
        else:
            print("no change since last backup")

    if not a.commit:
        return 0
    changed = subprocess.run(["git", "-C", REPO, "status", "--porcelain", DEST],
                             capture_output=True, text=True).stdout.strip()
    if not changed:
        print("nothing to commit")
        return 0
    subprocess.run(["git", "-C", REPO, "add", DEST], check=True)
    msg = (f"chore(tags): back up prod tag store "
           f"({after['tags']} tags, {after['assigned']} assigned)")
    subprocess.run(["git", "-C", REPO, "commit", "-q", "-m", msg], check=True)
    print("committed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
