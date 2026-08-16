# La-Mulana 2 tracker — design

A personal tracker for La-Mulana 2, mounted at `/lamulana`. It records what the
game told you (clues), what you are stuck on (threads), and what you have
collected (checklists), and it answers the one question the game keeps asking:
*what do I know that I have not spent yet?*

## Why this shape

La-Mulana 2's structure is that a tablet in one area describes something you can
only act on in another, often forty hours later. So the primitive is not "note",
it is a clue with a lifecycle and a link to the thing it unlocks. Everything in
this design exists to make three lists generatable rather than hand-maintained:

- open threads, by area
- clues you understand but have not used
- everything anchored to a single area, for when you warp somewhere

## Placement

A blueprint package `lamulana/` alongside `recipes/`, following its conventions
exactly:

```
lamulana/__init__.py     exports the blueprint
lamulana/db.py           schema, migrations, seed
lamulana/api.py          HTTP surface
lamulana/seed.py         canonical game data
templates/lamulana.html  the single-file frontend
data/lamulana.db         its own SQLite file
```

`server.py` gains one line registering the blueprint. Nothing is shared with the
soundboard except the Flask session.

Each request thread gets its own connection via the same `get_conn(path)`
pattern `recipes/db.py` uses; schema and seed run once at import on the
importing thread, not per-thread.

## Auth

Reads are open. Writes require `session['admin'] or session['can_edit']`, the
same check `recipes.api.can_edit()` makes, and return 403 otherwise. There is no
per-user data and no `who` attribution — this is a single-player tool.

## Data model

```sql
CREATE TABLE area (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL          -- rough progression order
);

CREATE TABLE clue (
    id             INTEGER PRIMARY KEY,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL DEFAULT '',   -- the text as the game gave it
    area_id        INTEGER REFERENCES area(id) ON DELETE SET NULL,
    room           TEXT,                       -- free text, autocompleted
    source         TEXT NOT NULL DEFAULT 'tablet',
    interpretation TEXT,                       -- what you think it means
    state          TEXT NOT NULL DEFAULT 'raw',
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE TABLE thread (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    area_id    INTEGER REFERENCES area(id) ON DELETE SET NULL,
    body       TEXT,                           -- what is blocking, what you suspect
    state      TEXT NOT NULL DEFAULT 'open',
    solution   TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    solved_at  INTEGER
);

CREATE TABLE clue_thread (
    clue_id   INTEGER NOT NULL REFERENCES clue(id) ON DELETE CASCADE,
    thread_id INTEGER NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
    PRIMARY KEY (clue_id, thread_id)
);

CREATE TABLE checklist_item (
    id         INTEGER PRIMARY KEY,
    group_name TEXT NOT NULL,
    name       TEXT NOT NULL,
    position   INTEGER NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    done_at    INTEGER,
    note       TEXT,
    UNIQUE (group_name, name COLLATE NOCASE)
);
```

`clue.source` is one of `tablet`, `npc`, `mail`, `other`.

`clue.state` is one of:

- `raw` — copied down, meaning unknown
- `understood` — you know what it says, you cannot act on it yet
- `used` — spent

`thread.state` is `open` or `solved`.

Clue-to-thread is many-to-many because La-Mulana 2 routinely splits one solution
across several tablets, and one tablet routinely feeds several puzzles.

### Solving a thread demotes its clues

The solve endpoint takes a `mark_clues_used` boolean, defaulted true, and the
solve dialog shows it as a checked box. Without this the ledger rots: you solve
things and never go back to mark the clues spent, and "clues I understand but
have not used" fills with lies until you stop trusting it.

### No tags

Area, state, and search cover every filter identified. Tags are the kind of
thing applied inconsistently at 1am mid-session. The schema can gain them later
without disturbing anything above.

### No FTS5

Expected volume is a few hundred rows. Search splits the query on whitespace and
ANDs each word as a `LIKE '%word%'` against title, body, and interpretation
(plus solution, for threads). That is instant at this scale, does not depend on
how the host's SQLite was compiled, and cannot silently drift out of sync the
way a trigger-maintained index can.

### Migrations

`init_schema()` calls `migrate()`, which walks an ordered, append-only
`MIGRATIONS` list and records its position in a `meta` table.

This is deliberately simpler than `recipes/db.py`, which additionally reflects
`SCHEMA` against the live database and ALTERs in missing columns. That engine
exists because recipes already had databases deployed in the house when columns
started being added to it. No `lamulana.db` exists anywhere yet, so every schema
change from here is a `MIGRATIONS` entry written with full knowledge of what is
on disk. If that stops being true, the right move is to extract recipes' engine
into a module both packages import — not to copy it.

## HTTP surface

All under `/lamulana`.

| Route | Method | Purpose |
| --- | --- | --- |
| `/` | GET | the page |
| `/api/bootstrap` | GET | areas, checklist groups, counts — one call on load |
| `/api/clues` | GET | list; filters `area`, `state`, `source`, `q` |
| `/api/clues` | POST | create |
| `/api/clues/<id>` | PATCH | edit any field, including `state` |
| `/api/clues/<id>` | DELETE | remove |
| `/api/threads` | GET | list; filters `area`, `state`, `q`; includes linked clue count |
| `/api/threads` | POST | create |
| `/api/threads/<id>` | GET | detail, with linked clues inlined in full |
| `/api/threads/<id>` | PATCH | edit |
| `/api/threads/<id>/solve` | POST | `{solution, mark_clues_used}` |
| `/api/threads/<id>` | DELETE | remove |
| `/api/link` | POST | `{clue_id, thread_id}` |
| `/api/link` | DELETE | `{clue_id, thread_id}` |
| `/api/search` | GET | `q` across clues and threads, returns both kinds |
| `/api/checklist` | GET | grouped, in seed order |
| `/api/checklist/<id>` | PATCH | toggle `done`, edit `note` |
| `/api/checklist` | POST | add a custom row to a group |
| `/api/checklist/<id>` | DELETE | remove a custom row |
| `/api/rooms` | GET | distinct room names, for autocomplete |

Writes return the full updated object so the frontend never re-fetches a list to
learn what it just did.

## Frontend

`templates/lamulana.html`, single file, vanilla JS, desktop-first. It reuses the
recipes palette with a warmer accent so it reads as the same site while being
instantly distinguishable.

Three tabs — **Clues**, **Threads**, **Progress** — with a search box pinned in
the header that searches across both kinds regardless of the active tab.

Clues and Threads share one layout: a narrow filter rail on the left (areas and
state chips, each with a count), a compact list in the middle, and a detail pane
on the right. Selection swaps the detail pane rather than navigating, so
scanning a list stays fast.

The Threads detail pane is the screen the tool exists for: title, your notes,
state, then **every linked clue rendered inline at full length**, so that when
you sit down to crack something, all the scattered text feeding it is on one
screen.

Progress is a dense single column of grouped checkboxes, each row with an
optional note ("behind the ice in Valhalla") and each group taking custom rows.

### Quick capture

`n` from anywhere opens a modal: area (pre-selected to the last area used, held
in `localStorage`), room (autocompleted from `/api/rooms`), title, body. This is
the most-travelled path in the app — two keystrokes and a paste.

### Keyboard

| Key | Action |
| --- | --- |
| `n` | new clue (quick capture) |
| `N` | new thread |
| `/` | focus search |
| `j` / `k` | move selection in the list (opens it in the detail pane immediately) |
| `l` | link selected clue to a thread (picker) |
| `1` `2` `3` | switch tab |
| `Esc` | close modal or clear search |

Keys are ignored while a text field has focus.

## Seed data

Before schema work, one research pass against the La-Mulana 2 wiki, cross-checked
against a second source, produces `lamulana/seed.py`:

- field/area names, in rough progression order
- guardians
- ankh jewels and sacred orbs (counts, and locations where documented)
- grail warp points
- apps and ROMs

**Anything not confirmable from two independent sources is left out rather than
guessed.** A seed with a wrong area name in it is worse than a short seed,
because a wrong name looks authoritative and gets filed against. Every checklist
group accepts custom rows precisely because the seed will be incomplete.

`seed.py` carries a comment naming its sources and the date pulled. Seeding is
idempotent and re-runnable: it inserts by `(group_name, name)` and never
overwrites a `done` flag or a note.

## Testing

`tests/test_lamulana_db.py`

- schema creates clean, and `init_schema` is re-runnable
- `_sync_columns` adds a column to an existing database
- seeding twice does not duplicate rows or clear `done`/`note`

`tests/test_lamulana_api.py`

- clue create → PATCH through `raw` → `understood` → `used`
- link and unlink; deleting a clue or thread cascades the link away
- thread solve with `mark_clues_used` true marks exactly the linked clues used,
  and false leaves them alone
- search tokenization: multi-word queries AND, and match across body and
  interpretation
- filters: area, state, source
- checklist toggle sets `done_at`; untoggle clears it; custom row add and delete
- every write route returns 403 without an editing session

Follows the existing `tests/conftest.py` patterns, with a temp `DATA_DIR` per
test module.

## Deployment

Production `server.py` has previously contained code present in no commit.
Registering the blueprint there is a one-line surgical edit made on the box, not
a file copy, and the repo is never rsynced over it. Restart per the existing
deploy notes.

## Out of scope

- screenshot or photo attachment on clues (adds upload and storage; revisit if
  typing tablet text turns out to be the bottleneck)
- multiple playthroughs — no `run_id` anywhere; a replay archives or resets
- a session journal
- any map rendering
- pre-loaded tablet text or hints — the point is that you record what you find
