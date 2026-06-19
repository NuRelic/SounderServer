# SounderServer — Favorites, Filters & Header UX overhaul

**Date:** 2026-06-19
**Status:** Approved (design)
**Mockup:** `~/dcc-clips/mock/mockup.html` (static, reuses the app's real CSS)

## Overview

Six changes to `templates/index.html` (frontend) and `server.py` (backend), kept
to the existing patterns. Goals: let editors reclassify clips between song/sound,
make Favorites default to a hand-arranged "deck", add a duration filter, tidy the
sort UX into Excel-style buttons, reclaim vertical space in the header, and drop
the NSFW gate.

Listening stays open (no auth); add/edit is gated by `can_edit()`, removal by admin —
all unchanged.

---

## Feature 1 — Move a clip between Song ⇄ Sound

**Today:** `long`/song is computed purely as `duration(fn) > LONG_THRESHOLD` (15s),
server-side, in two places: lane routing on fire (`server.py:351` `is_song`) and the
`long` flag in `/api/sounds` (`server.py:533`).

**Change:** add a persisted per-file override and route both checks through one helper.

- **Storage:** `data/type_overrides.json` → `{ "<file>": "song" | "sound" }`. Loaded at
  startup into `_TYPE_OVERRIDE` (dict), guarded by a lock consistent with the other
  `data/` stores.
- **Helper:** `is_long(fn) -> bool` returns `True` for `"song"`, `False` for `"sound"`,
  else `duration(fn) > LONG_THRESHOLD`. Replace both existing `dur > LONG_THRESHOLD`
  sites with `is_long(fn)`. Everything downstream (song lanes, Songs/Sounds filter, the
  Longest/Shortest sort) then honors the override automatically.
- **Endpoint:** `POST /api/sound_type` `{file, type}` where `type ∈ {auto, song, sound}`.
  `auto` removes the override (revert to the 15s rule). Gated by `can_edit()`. Persists
  and is reflected on the next `/api/sounds`. Shared/global (lanes are shared state).
- **UI:** a 3-way segmented control in the clip-editor modal — **Auto · 🎵 Song · Sound** —
  showing the current effective type. On change, POST `/api/sound_type`; on Save flow it
  refreshes sounds. Same edit gate as trim/rename.

## Feature 2 — Favorites "deck" model

**Today:** a user's favorites are one flat ordered list (`_FAVS_BY_USER[key] = [files]`);
membership = being in the list; order doubles as the custom order. The fav tab currently
defaults to "Most played".

**Change:** split into **favorites** (membership) + a **deck** (an ordered *subset* the
user has deliberately placed).

- **Storage:** migrate `_FAVS_BY_USER[key]` from `[files]` to `{ "favs": [files], "deck": [files] }`.
  - Migration: an existing list becomes `favs`; `deck` starts **empty**. So everyone
    starts "uncustomized" → alphabetical. (Backward-compatible loader: if the stored value
    is a list, wrap it.)
  - The existing list→dict and the old global-list→admin migration both still apply.
- **Favoriting** (`POST /api/favorite`) adds to `favs` only (never the deck). Unfavoriting
  removes from both `favs` and `deck`.
- **Deck setter:** `POST /api/favorites/order` now sets the **deck** (validated to be a
  subset of the user's real favorites; deduped). `GET /api/favorites` returns
  `{ favs, deck }` (both filtered to files still in the library).
- **"Has a custom view"** = `deck.length >= 1`.
- **Normal Favorites view** — new default sort value **`custom`**:
  - deck items first, in deck order, then the remaining favorites **alphabetically**.
  - deck empty → just alphabetical. No divider in normal view.
- **Edit-layout mode** — a horizontal **divider** splits the grid:
  - **Top = deck** (drag to reorder).
  - **Bottom = the rest** of favorites (alphabetical).
  - Drag bottom→top adds to the deck at the drop position; top→bottom removes from the
    deck (stays favorited). Persists via the deck setter.
- Default sort on the Favorites tab becomes **Custom** (was "Most played").

## Feature 3 — Letter filter forces A→Z, restores on "All"

- Track `PREV_SORT`. Entering a letter filter (ALLLETTER null→set) saves the current sort
  once and switches sort to **name (A→Z)**. Clicking **All** (clearing the letter) restores
  `PREV_SORT`.
- Works on both All and Favorites (Favorites: letter → A→Z, back to All → Custom/deck).
- The pre-filter sort is captured once on entering a letter; returning to All restores it.

## Feature 4 — Duration range filter (slider)

- A **dual-thumb min/max slider** in the filters band with a live **"X–Ys"** readout.
- State `DURMIN` / `DURMAX` (seconds). Filter: keep clips where `DURMIN <= dur <= DURMAX`.
- Track bounds `0 → ceil(max dur in library)` (computed from loaded sounds). Top thumb at
  max = "no upper cap" → readout shows `Ns+`.
- ANDs with all other filters (search, letter, Songs/Sounds, sort).
- `resetFilters()` snaps it back to the full range on tab switch; included in the
  `showFilters` gating.
- Implementation: two overlaid native `<input type=range>` + a fill bar + a readout label
  (no native dual-thumb exists). Kept lightweight.

## Feature 5 — Remove the NSFW button

- Remove the `🔞 NSFW` toggle button, the `NSFW_ON` state/localStorage, and the
  `if(!NSFW_ON) list = list.filter(s=>!s.nsfw)` line. All clips (incl. the 36 currently
  flagged) show for everyone.
- Leave the server-side `nsfw` field intact (harmless) so the concept can return later
  without data loss. No UI gate.

## Feature 6 — Header + toolbar redesign

**Today:** two stacked bars — `<header>` (title + name/login/sync/solo) and `.channels`
(lane occupancy + lane/volume controls) — then the tabs row, then the toolbar (search +
sort dropdown + type filter + NSFW + letters).

**Change:** collapse to fewer, denser rows.

- **Combined header (one row):** drop the "🔊 Sound Server" brand entirely.
  - **Left — "Now playing" 2-up grid:** flexes to consume **all** width the right controls
    don't need. Two boxes fill a row (each half); a lone box occupies one column (half), not
    stretched. Capped at **3 rows = up to 2 song lanes + 4 sound lanes**; collapses when idle.
    Song boxes accented (`--song`); idle lanes shown as dashed placeholders.
  - **Right — controls (single line, content-sized, never wraps):** name input · color
    swatch (a green `--accent2` border = signed in as **admin**, replacing the text badge) ·
    **🔁 Sync** and **🎧 Solo** as icon-only buttons with hover tooltips · compact volume ·
    **⚙** gear holding admin actions (lane counts, box volume, sync settings).
- **Tabs row — three grouped boxes** (bordered, clearly separated):
  - `[ All · ★ Favorites · ➕ Add/Edit ]`
  - `[ Sort: <buttons> ]` — see below
  - `[ <count> · ✏️ Edit ]` (right-aligned)
- **Sort = Excel-style tri-state buttons** (replaces the dropdown): `🔥 Played`, `A–Z`,
  `🆕 Added`, `⏱ Length`. Click cycles **off → ▼ (descending) → ▲ (ascending) → off**; only
  one active at a time. "Off" returns to the tab's natural order (Custom/deck on Favorites,
  server name-order on All). Field→direction mapping:
  - 🔥 Played: ▼ most→least, ▲ least→most
  - A–Z: ▲ A→Z, ▼ Z→A
  - 🆕 Added: ▼ newest, ▲ oldest (by `ver` = mtime)
  - ⏱ Length: ▼ longest, ▲ shortest (by effective `dur`)
- **Filters band (own row, visually separated):** `Filter` label · search · Songs/Sounds ·
  ⏱ Length slider · letter bar. (Sort no longer lives here.)
- Net chrome: **3 rows** (header / tabs+sort / filters) down from 4.

---

## Data model & migration summary

| Store | Before | After |
|-------|--------|-------|
| `data/favorites.json` | `{userkey: [files]}` | `{userkey: {favs:[...], deck:[...]}}` (list → `{favs:list, deck:[]}` on load) |
| `data/type_overrides.json` | — (new) | `{file: "song"\|"sound"}` |

Both loaders tolerate the old shape; no destructive migration. Deck starts empty for all
users (uncustomized → alphabetical).

## API changes

| Endpoint | Change |
|----------|--------|
| `GET /api/sounds` | `long` now uses `is_long()` (override-aware). No shape change. |
| `POST /api/sound_type` | **new** — `{file, type: auto\|song\|sound}`, `can_edit()`-gated. |
| `GET /api/favorites` | returns `{favs, deck}` (was `{order, favs}`). |
| `POST /api/favorite` | adds to `favs` only; unfavorite removes from `favs`+`deck`. |
| `POST /api/favorites/order` | now sets the **deck** (subset of favs). |

## Client state additions

`PREV_SORT` (letter-filter restore), `DURMIN`/`DURMAX` (duration filter), `DECK` (ordered
subset), new `SORT` value `custom`, sort direction state for the tri-state buttons. Folded
into `resetFilters()` where appropriate.

## Testing

- **Backend:** `is_long()` precedence (override beats duration, `auto` reverts); `/api/sound_type`
  gate + persistence + round-trip in `/api/sounds`; favorites loader migration (list → dict);
  favorite add lands in `favs` not `deck`; deck setter rejects non-favorites; unfavorite purges deck.
- **Frontend (manual against a local instance):** song/sound toggle re-routes lanes and the
  Songs/Sounds filter; Favorites default = deck-then-alpha, empty deck = alpha; deck edit
  divider drag add/remove persists; letter → A→Z then All restores prior sort; duration slider
  ANDs correctly and resets on tab switch; tri-state sort cycle (off→▼→▲→off, single-active);
  header collapses to one row when idle and controls never wrap; NSFW clips visible, no button.

## Out of scope

- Configurable duration buckets (slider only).
- Per-user song/sound overrides (it's shared/global).
- Re-seeding decks from existing favorite order (decks start empty).
- Reinstating an NSFW UI (field retained but unused).
