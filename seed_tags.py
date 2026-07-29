#!/usr/bin/env python3
"""One-shot tag seed: derive data/tags.json from filename prefixes.

Run once to bootstrap the tag vocabulary, then own it by hand in the editor.
This is deliberately NOT wired into scan_library() — continuous derivation
could never rename a cryptic slug, merge two prefixes that are one franchise,
or correct its own stop-word mistakes.

Usage:
    python3 seed_tags.py --from-url https://sounderserver.party   # inspect
    python3 seed_tags.py --write                                  # write data/tags.json

Refuses to overwrite an existing tags.json; move it aside first if you mean it.
"""
import argparse
import collections
import json
import os
import sys

MIN_GROUP = 3          # two clips sharing a prefix is a coincidence, not a tag

# Prefixes that are the first word of a phrase rather than a tag. Derived by
# reviewing every group this rule rejects — a word list alone is not enough.
# `hm` was on this list once and wrongly ate the 13-clip Hotline Miami
# soundtrack, which is why the rejects get eyeballed rather than trusted.
STOP = {
    "the", "you", "its", "no", "hey", "thats", "damn", "i", "a", "and", "my",
    "we", "he", "she", "it", "is", "are", "was", "get", "got", "let", "lets",
    "dont", "what", "why", "how", "when", "who", "all", "one", "two", "not",
    "so", "oh", "well", "yes", "yeah", "ok", "okay", "this", "that", "they",
    "them", "can", "will", "just", "now", "out", "up", "down", "on", "off",
    "in", "of", "to", "for", "with", "be", "do", "go", "me", "him", "her",
    "us", "if", "or", "but", "as", "at", "by", "from", "have", "has", "had",
    "more", "most", "some", "any", "only", "very", "too", "then", "than",
    "there", "here", "over", "under", "back", "again", "still", "even",
    "make", "made", "take", "look", "see", "know", "think", "say", "said",
    "come", "came", "give", "want", "need", "feel", "good", "bad", "big",
    "little", "long", "new", "old", "right", "wrong", "yo", "uh", "um", "ah",
}

# child slug -> parent slug. One level only; nothing in the library needs more.
PARENTS = {
    "dcca": "dcc", "dccc": "dcc", "dccd": "dcc", "dccm": "dcc", "dcco": "dcc",
    "p3": "persona", "p4": "persona", "p5": "persona",
    "ff7": "finalfantasy", "ff8": "finalfantasy", "ffx": "finalfantasy",
    "ff": "finalfantasy",          # ff1_/ff6_/ff9_/ff10_, found by the stem rule
    "fma": "fmaseries", "fmab": "fmaseries",
    "hm": "hotline", "hm2": "hotline",
    "epic": "e",                   # the handful still on the old epic_NN_ naming
}

LABELS = {
    # Dungeon Crawler Carl — the 4th letter is the speaker, confirmed with Brandon
    "dcc": "Dungeon Crawler Carl", "dcca": "The AI", "dccc": "Carl",
    "dccd": "Donut", "dccm": "Mordecai", "dcco": "Other",
    "persona": "Persona", "p3": "Persona 3", "p4": "Persona 4", "p5": "Persona 5",
    "finalfantasy": "Final Fantasy", "ff7": "Final Fantasy VII",
    "ff8": "Final Fantasy VIII", "ffx": "Final Fantasy X",
    "fmaseries": "Fullmetal Alchemist", "fma": "Fullmetal Alchemist",
    "fmab": "FMA: Brotherhood",
    "hotline": "Hotline Miami", "hm": "Hotline Miami", "hm2": "Hotline Miami 2",
    "ut": "Undertale", "lk": "Letterkenny", "mew": "Mewgenics",
    "og": "OG", "oracle": "Oracle", "athf": "Aqua Teen Hunger Force",
    "rnm": "Rick & Morty", "nier": "NieR", "d": "Disney", "au": "Among Us",
    "dn": "Death Note", "sb": "SpongeBob",
    "e": "EPIC: The Musical", "epic": "EPIC (older epic_NN_ files)",
    "ff": "Final Fantasy (numbered)",
    "sw": "Star Wars", "simp": "Simpsonwave", "loz": "Legend of Zelda",
    "ct": "Chrono Trigger", "hk": "Hollow Knight", "dbz": "Dragon Ball Z",
    "pkmn": "Pokémon", "ygo": "Yu-Gi-Oh", "su": "Steven Universe",
    "nw": "Neature Walk", "sfx": "SFX", "glass": "Glass Animals",
    "daft": "Daft Punk", "chipmunks": "Chipmunks", "2001": "2001: A Space Odyssey",
    "ow": "Overwatch", "sonic": "Sonic", "mario": "Mario",
}


def prefix_of(name):
    """Lowercased prefix before the first underscore, or None."""
    return name.split("_")[0].lower() if "_" in name else None


def _pairs(items):
    """Normalise input to (display name, filename) pairs.

    Grouping keys off the name (no extension) but `assign` must key off the
    filename, because that is what _LIBRARY and favorites.json use — a store
    keyed by name looks entirely like ghost entries and filters to nothing.
    Bare strings are treated as both, which keeps the unit tests readable.
    """
    for it in items:
        if isinstance(it, str):
            yield it, it
        else:
            yield it["name"], it["file"]


def derive(items):
    """Build {"tags": {...}, "assign": {...}} from clips.

    `items` may be clip names, or dicts with "name" and "file".
    """
    pairs = list(_pairs(items))
    fileof = {n: f for n, f in pairs}
    names = [n for n, _ in pairs]
    groups = collections.defaultdict(list)
    for n in names:
        p = prefix_of(n)
        if p:
            groups[p].append(n)

    kept = {p: v for p, v in groups.items()
            if len(v) >= MIN_GROUP and p not in STOP}

    # Numbered-prefix families: e17_/e18_/e20_ are each a group of one, so the
    # rule above drops them, but they're one set with the number in the prefix
    # rather than after it. Group the leftovers by their digit-stripped stem
    # and keep a stem that has MIN_GROUP distinct numbers behind it. Only
    # leftovers are considered, so p3/p4/p5 — real tags on their own — never
    # collapse into a "p" stem.
    stems = collections.defaultdict(dict)
    for p, v in groups.items():
        if p in kept or p in STOP:
            continue
        stem = p.rstrip("0123456789")
        if stem and stem != p and stem not in kept:
            stems[stem][p] = v
    for stem, numbered in stems.items():
        if len(numbered) >= MIN_GROUP:
            kept[stem] = [n for v in numbered.values() for n in v]

    tags, assign = {}, {}
    for slug, members in kept.items():
        tags[slug] = {"label": LABELS.get(slug, slug)}
        parent = PARENTS.get(slug)
        if parent:
            tags[slug]["parent"] = parent
            tags.setdefault(parent, {"label": LABELS.get(parent, parent)})
        for n in members:
            assign.setdefault(fileof[n], []).append(slug)

    # A clip whose own prefix is a parent slug belongs directly on that parent,
    # even though its group is too small to survive the threshold above:
    # dcc_class_selection and persona_chill_lofi_1hr are each a group of one.
    parent_slugs = {s for s in tags if s not in PARENTS}
    for n in names:
        p = prefix_of(n)
        if p and p in parent_slugs and fileof[n] not in assign:
            assign[fileof[n]] = [p]

    return {"tags": tags, "assign": assign}


def rejected(items):
    """Groups big enough to be tags but dropped as stop-words — review these."""
    groups = collections.defaultdict(list)
    for n, _ in _pairs(items):
        p = prefix_of(n)
        if p:
            groups[p].append(n)
    return {p: v for p, v in groups.items() if len(v) >= MIN_GROUP and p in STOP}


def _load_names(url):
    # Cloudflare 403s urllib's default User-Agent, so send a real one.
    from urllib.request import Request, urlopen
    req = Request(url.rstrip("/") + "/api/sounds",
                  headers={"User-Agent": "seed_tags/1.0", "Accept": "application/json"})
    with urlopen(req, timeout=30) as r:
        return [{"name": s["name"], "file": s["file"]} for s in json.load(r)["sounds"]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-url", default="https://sounderserver.party")
    ap.add_argument("--write", action="store_true", help="write data/tags.json")
    ap.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "data"))
    a = ap.parse_args(argv)

    names = _load_names(a.from_url)
    out = derive(names)
    tops = [s for s, t in out["tags"].items() if "parent" not in t]
    print(f"{len(names)} clips -> {len(out['tags'])} tags "
          f"({len(tops)} top-level), {len(out['assign'])} clips assigned")

    rej = rejected(names)
    if rej:
        print("\nrejected as stop-words (confirm none of these is a real tag):")
        for p, v in sorted(rej.items(), key=lambda x: -len(x[1])):
            print(f"  {len(v):3d}  {p:10s} {', '.join(sorted(v)[:4])}")

    if not a.write:
        print("\n(dry run — pass --write to save)")
        return 0

    path = os.path.join(a.data_dir, "tags.json")
    if os.path.exists(path):
        print(f"\nrefusing to overwrite {path} — move it aside first", file=sys.stderr)
        return 1
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, path)
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
