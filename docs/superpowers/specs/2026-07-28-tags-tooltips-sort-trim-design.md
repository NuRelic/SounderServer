# Tags, Tooltips, Natural Sort, Trim Precision

Design doc — 2026-07-28

Four changes to the soundboard, shipped as four independent deploys. The
headline feature is a tag system for filtering a 1,994-clip library. Alongside
it: full names on hover, alphanumeric sorting, and a fix for the clip editor
cutting audio at the wrong place.

## Why

The library crossed ~2,000 clips. The only ways to narrow it are a substring
search, an A–Z letter bar, a length range, and a song/sound toggle. None of
those help when what you want is "the Undertale stuff" or "Donut lines" — the
grouping is encoded in filename prefixes that the UI does nothing with.

Three smaller problems surfaced while scoping that one:

- Tile names ellipsize at one line, so long names are unreadable and there is no
  way to see the rest.
- Sorting is `localeCompare`, so `sheik10` sorts before `sheik2`.
- The clip editor cuts audio at a different position than the one previewed.

## Constraints

- **Prod is the source of truth.** Analysis in this doc is from a live
  `/api/sounds` pull on 2026-07-27: 1,994 clips.
- **The library is the schema.** Tags must be derived from filenames as they
  exist; no bulk rename.
- **Lanes interrupt.** `fire()` at `server.py:388` gives each lane one sound; a
  new fire in the same lane kills the previous. Two sound lanes, one song lane.
- **Playing stays open to everyone.** Only add/edit is gated by
  `can_edit()`. Tag filtering is playing, not editing.
- **Deploys are independent.** Each of the four ships and is verified on its
  own. Deploy 1 depends on nothing.

## Non-goals

Explicitly dropped after scoping, recorded so they don't get relitigated:

- **Play all / shuffle all / play random from a tag.** Originally requested,
  then cut. With one sound per lane, "play all" of 64 clips can only mean a
  sequential queue, and a queue that behaves like the rest of the app has to
  live server-side — a scheduler, shared state, stop semantics, and
  contention rules for two people queueing at once. That is a larger feature
  than the filter it was attached to. Tags are a filter; you pick from the
  filtered grid and fire clips the way you already do.
- **Renaming files to normalize prefixes.** The tag store maps onto filenames
  as they are.
- **Tagging all 1,994 clips.** The seed covers the 642 that carry a usable
  prefix. The rest are taggable by hand once the editor exists, and it is fine
  if most never are.

---

## Part 1 — Tags

### What the library actually contains

Of 1,994 clips, 999 have an `_`. Prefixes with 3+ members cover 665 clips.
Filtering English sentence-starters (`the_`, `you_`, `its_`, `no_`, `hey_`,
`thats_`, `damn_`) leaves **68 tags covering 640 clips**.

The stop-word list needs human review, not just a word list. A first pass
rejected `hm` — 13 Hotline Miami soundtrack clips — because "hm" reads as a
filler word. That single false positive is the argument for the decision below.

Two structures the flat prefix rule misses:

- **Two-level prefixes** — `d_herc_`, `d_enc_`, `d_moana_` (Disney → film);
  `simp_wave_`.
- **Numeric families with no underscore at all** — `BF1`–`BF8`, `jr1`–`jr8`,
  `Fortune1`–`4`, `quiznos1`–`5`. 28 families, 110 clips. Out of scope for the
  seed; reachable by hand in deploy 4.

### Decisions

#### Seed once into an editable store, don't derive continuously

The importer runs **once** and writes a store that is then owned by hand.

*Rejected: recompute tags from prefixes on every `scan_library()`.* Zero
maintenance and never stale, but it cannot rename `dcca` to something a human
recognizes, cannot merge `hm` + `hm2` into one franchise, cannot fix its own
stop-word mistakes, and permanently locks out the 995 clips with no underscore.
The `hm` false positive would be unfixable by design.

*Rejected: manual from empty.* ~640 clips tagged by hand before the feature does
anything.

New uploads get a *suggested* tag from their prefix, surfaced in the editor.
They never silently create a tag.

#### Tags are a filter dimension, not a new way to play

The Tags tab lands on a card board. Tapping a card sets an active tag and drops
into an ordinary grid — same tiles, same lane zones, same ★, same tap-to-fire.

This makes the filter one more predicate in the existing chain at
`index.html:800-806`, so it stacks with search, length, and song/sound for free.

*Rejected: a chip rail beside the A–Z bar.* Cheaper, but 58 top-level tags is
too many to scan horizontally, and the whole point is discovering what exists.

#### Optional parent, one level deep

```json
"tags": {
  "dcc":   {"label": "Dungeon Crawler Carl"},
  "dcc-d": {"label": "Donut", "parent": "dcc"}
}
```

The board shows top-level tags only. DCC is **one card reading 177 clips**, not
five. Drilling in shows character sub-tags as a chip rail above the grid: land
on all 177, tap Donut to narrow to 54.

A parent holds clips **directly** as well as through children —
`dcc_class_selection` is a bare `dcc` clip with no character letter.

This collapses the board from 68 cards to 58:

| Parent | Children |
|---|---|
| Dungeon Crawler Carl | `dcca` `dccc` `dccd` `dccm` `dcco` |
| Persona | `p3` `p4` `p5` |
| Final Fantasy | `ff7` `ff8` `ffx` |
| Fullmetal Alchemist | `fma` `fmab` |
| Hotline Miami | `hm` `hm2` |

Depth stops at one level. Nothing in the library needs more.

#### Multi-tag

`assign` maps a filename to a **list**. Single-tag is a subset of multi-tag and
a list costs nothing at this size.

#### JSON, not SQLite

`data/tags.json`, via the existing `_load`/`_save` helpers, alongside
`favorites.json`, `limits.json`, `user_colors.json`.

*Rejected: a table in `catalog.db`.* ~700 entries doesn't justify it, and
`catalog.db` carries a legacy schema inherited from the original board. The
recipes work already showed SQLite here needs per-thread connections
(commit `78dab75`) — real complexity for no gain at this size.

### Data model

```json
{
  "tags": {
    "dcc":   {"label": "Dungeon Crawler Carl"},
    "dcc-a": {"label": "The AI",   "parent": "dcc"},
    "dcc-c": {"label": "Carl",     "parent": "dcc"},
    "dcc-d": {"label": "Donut",    "parent": "dcc"},
    "dcc-m": {"label": "Mordecai", "parent": "dcc"},
    "dcc-o": {"label": "Other",    "parent": "dcc"}
  },
  "assign": {
    "dccd_my_butt.wav": ["dcc-d"],
    "dcc_class_selection.wav": ["dcc"]
  }
}
```

Keyed by filename, matching `_LIBRARY` and `favorites.json`. Two integration
points follow from that and must be handled explicitly:

- **Rename.** `catalog_rename()` at `server.py:322` already repoints a renamed
  file's soundid so play stats survive. Tag assignments must be repointed in the
  same place, or a rename silently unfiles the clip.
- **Delete.** Reads must filter to files still in `_LIBRARY`, the way
  `/api/favorites` does at `server.py:702`. Otherwise deleted clips leave ghost
  assignments that inflate every count on the board.

A clip assigned to a child is **not** also assigned to its parent. Parent
membership is computed as `own ∪ all descendants` at read time, so re-parenting
a tag never requires rewriting assignments.

### Seed importer

A one-shot script, not a startup path. Steps:

1. Pull the live library.
2. Group by lowercased prefix before the first `_`.
3. Drop groups smaller than 3.
4. Drop stop-words — **with the list reviewed by hand against the groups it
   rejects**, which is how `hm` gets caught.
5. Apply the parent map above, creating the five parent tags.
6. **Sweep for bare-parent clips.** A clip whose own prefix equals a parent slug
   is assigned directly to that parent, regardless of group size. Without this
   step the size-3 threshold in step 3 drops them: `dcc_class_selection` and
   `persona_chill_lofi_1hr` are each a group of one.
7. Label from a hand-written table; fall back to the raw slug.
8. Write `data/tags.json`. Refuse to overwrite an existing file.

Expected: 68 tags, 58 top-level, 642 clips assigned.

DCC character mapping, confirmed with the user: `a` = the AI, `c` = Carl,
`d` = Donut, `m` = Mordecai, `o` = Other.

### API

| Route | Method | Auth | Purpose |
|---|---|---|---|
| `/api/tags` | GET | open | Tags with counts, plus `assign` |
| `/api/tags` | POST | `can_edit()` | Create / rename / re-parent / delete |
| `/api/tags/assign` | POST | `can_edit()` | Set a clip's tags |

`GET /api/tags` returns counts already rolled up through parents and already
filtered to files present in `_LIBRARY`, so the client never computes membership
itself.

Deploy 3 ships the GET. Deploy 4 ships both POSTs.

### UI

**Card board** — third tab beside All / ★ Favorites. Cards show label, clip
count, and song count where non-zero. Sorted by count descending.

**Drill-in** — a header with a back control and the tag name, a sub-tag chip
rail when the tag has children, then the ordinary grid. The active tag composes
with every existing filter.

**Editor (deploy 4)** — a list of tags with editable labels, a parent picker, a
merge action, and a per-clip tag control in the existing clip editor.

Mockups, on prod, no restart required:

- `https://new.sounderserver.party/static/tags.html` — the three layout options
- `https://new.sounderserver.party/static/tags2.html` — the chosen shape

---

## Part 2 — Tooltips

Tile names ellipsize at `index.html:122`; feed chips at 43, edit rows at 216,
lookup rows at 252, now-playing at 347.

**Both mechanisms, deliberately.** A native `title` on every truncating element
— two lines, universal, works in the feed and the `!` lookup. Plus a styled CSS
tooltip on grid tiles specifically, where browsing actually happens: instant
instead of ~1s, on-theme, and reachable by long-press on touch.

*Rejected: a JS tooltip library.* Nothing here needs positioning logic.

Tooltips carry the **full name**, which is the filename stem — the same string
`!`-commands use.

---

## Part 3 — Natural sort

Not hypothetical: **70 of 1,994 names sort wrong today**. `sheik10` sorts before
`sheik2`.

One comparator — split on digit runs, compare digit runs numerically and
everything else with `localeCompare` — applied at three call sites:

- Grid sort, `index.html:806-808` (`name` / `name_desc`)
- `!` lookup ranking, `index.html:1611`
- Add/Edit list, `index.html:1746`

The favorites deck order is user-defined and must not change.

This also covers the `e1_…e33_` case that prompted the request.

---

## Part 4 — Trim precision

### What was measured

Symptom: exact numbers entered, verified in the preview, saved, output shifted.

A click track with 40 ms bursts starting at exactly 1.0 s … 8.0 s was encoded to
MP3 and cut with the server's exact command from `server.py:1020`:

```
ref.mp3   onsets = 1.0001  2.0001  3.0001  4.0001 ...
ffmpeg -i ref.mp3 -ss 2.0 -t 2.0 cur.mp3
cur.mp3   onsets = 0.0001  1.0001          <- 0.07 ms error
```

Sample-accurate, and still accurate after three stacked generations of
re-cutting — the "made from an existing sound" case. **`-ss` placement and MP3
encoder delay are both ruled out.**

### Root cause

The editor displays a timeline nobody cuts on. Four clocks:

| # | Clock | Where |
|---|---|---|
| 1 | Web Audio PCM — drives the waveform *and* the typed start/end values | `index.html:984` |
| 2 | Media-element time — drives ▶ Preview | `index.html:1077` |
| 3 | Container time — what ffmpeg cuts | `server.py:1020` |
| 4 | ffprobe duration — silently clamps `end` | `server.py:1002` |

Clocks 1 and 2 disagree on MP3 because Web Audio and the media element handle
encoder delay differently. Clock 4 truncates without telling anyone.

There is also a plain bug at `index.html:1079`: `prevAudio.currentTime =
regStart` is set on a fresh `Audio` **before metadata loads**, so the seek is
unreliable. The same file does it correctly at line 1277 via a `loadedmetadata`
listener. Preview may therefore have been playing from 0 while appearing to
confirm the selection.

### Fix

Collapse four clocks to one — the decoded PCM timeline the waveform already
shows.

1. **Preview from `EDITBUF`** via `AudioBufferSourceNode.start(0, regStart,
   regEnd - regStart)`. Preview becomes literally the samples that will be cut.
   This also removes the line 1079 bug rather than patching it.
2. **Cut with `atrim`** on the decoded sample timeline:
   `-af "atrim=start=S:end=E,asetpts=N/SR/TB"`, composed with the existing
   `volume` filter.
3. **Return the authoritative duration** from `/api/edit` and reject rather than
   silently clamp when `end` exceeds it.

### Open

One shifted clip — source, numbers typed, observed offset. A **constant** offset
(~25 ms regardless of position) implicates clocks 1 vs 3; an offset that
**grows** deeper into the file implicates 1 vs 4. Without it, deploy 2 ships
defensively against both. This gates only deploy 2.

---

## Deploys

| # | Deploy | Contents | Risk |
|---|---|---|---|
| 1 | Quick wins | Natural sort at three call sites; tooltips on all truncating elements | Low — display only |
| 2 | Trim precision | Preview from `EDITBUF`; `atrim` cut; authoritative duration | Medium — writes audio files |
| 3 | Tags core | Store, seed importer, `GET /api/tags`, Tags tab, board, drill-in | Medium — new tab, new state |
| 4 | Tags depth | Editor, rename/merge/re-parent, per-clip tagging, rename/delete integration | Low — gated behind `can_edit()` |

## Testing

Deploy 1 — unit tests for the comparator, including `sheik2` < `sheik10`,
`e1` < `e33`, equal-prefix ties, and names with no digits. Visual check that the
favorites deck order is unchanged.

Deploy 2 — the click-track harness becomes a regression test: generate, cut at
known offsets, assert onsets land within 1 ms. Cover WAV and MP3, and a
second-generation cut. This is the highest-value test in the batch because it is
what caught the original wrong hypothesis.

Deploy 3 — importer tests on a fixture library: stop-word rejection, the
`hm` case specifically, parent rollup, and `dcc_class_selection` landing on the
parent. API tests that counts exclude files absent from `_LIBRARY`.

Deploy 4 — rename repoints assignments; delete drops them; merge preserves
membership; re-parenting doesn't rewrite `assign`.

Every deploy gets a prod smoke check: board loads, a clip fires, counts are
sane.

## Risks

- **The seed is only as good as the stop-word list.** Mitigated by reviewing
  rejects by hand and by the fact that every mistake is fixable in the editor.
- **`data/tags.json` is excluded from rsync** along with the rest of `data/`,
  so it is prod-owned state. *Resolved:* `backup_tags.py` pulls it into
  `backups/tags.json` and can commit; `save_tags()` also keeps 24 throttled
  rolling snapshots in `data/tags-history/`. Prod stays authoritative — the
  repo copy is a backup, never a source.
- **Tagging decays as the library grows.** Every clip added by upload or URL
  fetch used to land untagged forever. *Resolved:* `tags_autotag()` files new
  clips from a prefix index learned from existing assignments, hooked into all
  three paths a file can arrive by. It only acts on an unambiguous prefix and
  never touches a clip that already has tags.
- **Re-deriving can undo curation.** The seed refused to overwrite, so it
  could not be re-run at all. *Resolved:* `--merge` is additive — it defers to
  whichever tag already owns a prefix (so `bb_*` files join Bob's Burgers
  rather than growing a duplicate `bb` tag) and honours a `retired` list so a
  deliberately deleted tag cannot come back. The server carries `retired`
  through `_norm_tags()`, or the next save would erase it.
- **`static/` on prod is not in the repo.** Mockups live there and are not
  version-controlled. Fine for mockups; nothing load-bearing goes there.
