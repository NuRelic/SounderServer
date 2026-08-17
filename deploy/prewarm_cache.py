#!/usr/bin/env python3
"""Pre-fill a node's audio cache with the sounds people actually play.

Why: the node only downloads a sound the first time it's fired, and that download
is what makes the first play late (a 95MB song takes ~20s on the kitchen's link).
Doing it ahead of time, on YOUR fast network before the box ever leaves, means the
node is instantly responsive from its first minute in someone else's house.

Ranks the whole library by all-time play count and fills up to a byte budget, so
the bytes go to what gets fired instead of the alphabetical first N.

Run it AS THE USER THAT RUNS THE AGENT (it writes into that user's cache):
    python3 prewarm_cache.py                     # 75% of the node's cache cap
    python3 prewarm_cache.py --budget-mb 600
    python3 prewarm_cache.py --include shorts    # clips only — cheap and high value
    python3 prewarm_cache.py --dry-run

It imports kitchen_agent to reuse that module's cache-key scheme and download
path, so the files land exactly where the agent will look for them. Any drift
between the two is impossible by construction.
"""
import os, sys, json, argparse, urllib.request

# Must be set BEFORE importing the agent: importing it calls pygame.mixer.init(),
# and we have no business grabbing the real audio device just to fill a cache.
os.environ["SS_DRIVER"] = "dummy"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import kitchen_agent as ka
except Exception as e:
    sys.exit("could not import kitchen_agent.py (must sit next to this script): %s" % e)


def human(n):
    return "%.1f MB" % (n / 1048576.0) if n >= 1048576 else "%.0f KB" % (n / 1024.0)


def main():
    p = argparse.ArgumentParser(description="Pre-fill a node's audio cache by play count.")
    p.add_argument("--budget-mb", type=int, default=None,
                   help="bytes to spend (default: 75%% of the node's cache cap)")
    p.add_argument("--include", choices=("all", "shorts", "songs"), default="all",
                   help="shorts = clips only (cheap, high value); songs = long tracks only")
    p.add_argument("--top", type=int, default=0, help="only consider the N most-played files")
    p.add_argument("--max-file-mb", type=int, default=0, help="skip files bigger than this")
    p.add_argument("--min-plays", type=int, default=1, help="skip files played fewer times")
    p.add_argument("--dry-run", action="store_true", help="show the plan, download nothing")
    args = p.parse_args()

    budget = (args.budget_mb * 1024**2) if args.budget_mb else int(ka.CACHE_CAP * 0.75)

    print("server:  %s%s" % (ka.SERVER, " (origin-pinned %s)" % ka.ORIGIN_IP if ka.ORIGIN_IP else ""))
    print("cache:   %s   cap %s, budget %s"
          % (ka.CACHE, human(ka.CACHE_CAP), human(budget)))

    try:
        req = urllib.request.Request(ka.SERVER + "/api/sounds")
        sounds = json.loads(ka._opener_dl.open(req, timeout=60).read())["sounds"]
    except Exception as e:
        sys.exit("could not fetch the library: %s" % e)

    total_plays = sum(s.get("plays", 0) for s in sounds) or 1
    ranked = sorted(sounds, key=lambda s: -s.get("plays", 0))
    if args.include == "shorts":
        ranked = [s for s in ranked if not s.get("long")]
    elif args.include == "songs":
        ranked = [s for s in ranked if s.get("long")]
    ranked = [s for s in ranked if s.get("plays", 0) >= args.min_plays]
    if args.top:
        ranked = ranked[:args.top]
    print("library: %d files, %d considered, %d all-time plays\n"
          % (len(sounds), len(ranked), total_plays))

    spent = have = 0
    got = skipped_big = failed = 0
    plays_covered = 0
    max_file = args.max_file_mb * 1024**2 if args.max_file_mb else 0

    for s in ranked:
        fn, ver = s["file"], s.get("ver", 0)
        path = ka.cache_path(fn, ver)
        if os.path.exists(path):                      # already cached from a previous run
            sz = os.path.getsize(path)
            have += sz; spent += sz
            plays_covered += s.get("plays", 0)
            continue
        if spent >= budget:
            continue
        if args.dry_run:
            # No size known without fetching; assume the duration-based estimate is
            # good enough to show a plan (mp3 at ~128kbps ≈ 16KB/s).
            est = int(max(s.get("dur", 0), 0.5) * 16000)
            if max_file and est > max_file:
                skipped_big += 1; continue
            if spent + est > budget:
                continue
            spent += est; got += 1; plays_covered += s.get("plays", 0)
            continue
        try:
            ka._download(fn, ver, path)
            sz = os.path.getsize(path)
            if max_file and sz > max_file:
                os.remove(path); skipped_big += 1; continue
            spent += sz; got += 1
            plays_covered += s.get("plays", 0)
            if got % 25 == 0:
                print("  ... %d cached, %s used (%.0f%% of budget)"
                      % (got, human(spent), 100.0 * spent / budget))
        except Exception as e:
            failed += 1
            print("  ! %s: %s" % (fn, str(e)[:70]))

    print("\n--- %s ---" % ("PLAN" if args.dry_run else "RESULT"))
    print("newly cached:      %d" % got)
    if have:
        print("already cached:    %s" % human(have))
    if skipped_big:
        print("skipped (too big): %d" % skipped_big)
    if failed:
        print("failed:            %d" % failed)
    print("cache used:        %s of %s budget (cap %s)"
          % (human(spent), human(budget), human(ka.CACHE_CAP)))
    print("play coverage:     %.1f%% of all historical plays are now a cache hit"
          % (100.0 * plays_covered / total_plays))
    if args.dry_run:
        print("\n(estimate only — sizes are guessed from duration. Re-run without --dry-run.)")


if __name__ == "__main__":
    main()
