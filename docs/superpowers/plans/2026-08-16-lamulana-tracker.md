# La-Mulana 2 Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/lamulana` page on SounderServer that records La-Mulana 2 clues, links them to the things you're stuck on, and answers "what do I know that I haven't spent yet?"

**Architecture:** A Flask blueprint package `lamulana/` alongside the existing `recipes/`, with its own SQLite database at `data/lamulana.db` and its own single-file template. Clues carry a three-state lifecycle and link many-to-many to threads; the useful lists are SQL queries over that, not stored state. Reads are open, writes require an editing session.

**Tech Stack:** Python 3, Flask blueprints, SQLite (stdlib `sqlite3`, WAL, per-thread connections), pytest, vanilla JS + hand-written CSS in one template.

**Spec:** `docs/superpowers/specs/2026-08-16-lamulana-tracker-design.md`

---

## File Structure

| File | Responsibility |
| --- | --- |
| `lamulana/__init__.py` | exports `lamulana_bp` |
| `lamulana/seed.py` | canonical La-Mulana 2 data: areas and checklist groups. Pure data, no imports. |
| `lamulana/db.py` | connection lifecycle, `SCHEMA`, `MIGRATIONS`, seeding |
| `lamulana/api.py` | the blueprint: page route + JSON routes |
| `templates/lamulana.html` | the whole frontend, one file |
| `server.py` | two added lines registering the blueprint |
| `tests/conftest.py` | add `lamulana` to the sys.modules scrub, add a `lamulana_db` fixture |
| `tests/test_lamulana_db.py` | schema, migrations, seed idempotence |
| `tests/test_lamulana_api.py` | routes, lifecycle, linking, solve cascade, search, auth |

`api.py` is the only file that will get long. If it passes ~600 lines, split the
checklist routes into `lamulana/checklist.py` — they share nothing with clues and
threads but the connection.

---

## Task 1: Seed data

The lists below were read from the official La-Mulana 2 wiki
(`la-mulana2.fandom.com`) on 2026-08-16. Deliberately **not** seeded: app
combinations (that's a puzzle the player solves), Ankh Jewels (the wiki states
"each area has one ankh and one ankh jewel" but never gives a total, and
secondary sources disagree between 9 and 10), and Holy Grail warp points (never
enumerated as a list).

**Files:**
- Create: `lamulana/seed.py`
- Create: `lamulana/__init__.py`
- Test: `tests/test_lamulana_db.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_lamulana_db.py`:

```python
from lamulana import seed


def test_every_area_is_named_once():
    assert len(seed.AREAS) == 28
    assert len(set(seed.AREAS)) == len(seed.AREAS)


def test_areas_include_both_halves_of_the_game():
    # Eg-Lana's own fields and the La-Mulana ruins revisited later both count.
    assert "Immortal Battlefield" in seed.AREAS
    assert "Gate of Guidance" in seed.AREAS


def test_checklist_groups_have_unique_names_within_a_group():
    for group, items in seed.CHECKLIST:
        assert len(set(items)) == len(items), f"duplicate row in {group}"


def test_checklist_group_sizes():
    sizes = {group: len(items) for group, items in seed.CHECKLIST}
    assert sizes == {
        "Guardians": 10,
        "Sacred Orbs": 10,
        "Mantras": 10,
        "Maps": 16,
        "Apps": 24,
    }


def test_non_ascii_row_names_are_intact():
    # A bad re-encode mangles these silently -- every structural assertion
    # above still passes on "MÃ³Ã°ir". The em-dash matters too: it separates
    # the name from the location in every Guardian and Mantra row.
    mantras = dict(seed.CHECKLIST)["Mantras"]
    assert "Iorð — Annwfn (D-4)" in mantras
    assert "Sær — Shrine of the Frost Giants (C-3)" in mantras
    assert "Móðir — Eternal Prison - Gloom (C-5)" in mantras
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/brandonnowlin/Documents/GitHub/SounderServer && .venv/bin/python -m pytest tests/test_lamulana_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lamulana'`

- [ ] **Step 3: Write the seed module**

Create `lamulana/__init__.py`:

```python
"""La-Mulana 2 tracker — a blueprint inside the SounderServer app.

Mounted at /lamulana. Owns its own SQLite database and shares nothing with the
soundboard or the recipes blueprint except the Flask session, which is what
gates writes.
"""

from .api import bp as lamulana_bp

__all__ = ["lamulana_bp"]
```

Create `lamulana/seed.py`:

```python
"""Canonical La-Mulana 2 data.

Read from the official wiki (https://la-mulana2.fandom.com) on 2026-08-16:
Category:Fields, Category:Guardians, Treasures, Mantras, and Applications.

Anything the wiki does not state outright is left out rather than guessed. That
covers Ankh Jewels (no total is given anywhere, and secondary sources split
between 9 and 10) and Holy Grail warp points (never enumerated). App
combinations are known but deliberately absent: working them out is the game.

Every checklist group accepts user-added rows, because this list is a starting
point and not a claim to be complete.
"""

# Ordered in five bands rather than a precise progression order, which varies
# by route. The Village first, then the nine "frontside" fields -- identified as
# frontside by each holding one of the ten Sacred Orbs -- then the later fields,
# the connecting sub-areas, and the La-Mulana ruins revisited in the back half.
AREAS = [
    "Village of Departure",

    "Roots of Yggdrasil",
    "Annwfn",
    "Immortal Battlefield",
    "Icefire Treetop",
    "Divine Fortress",
    "Shrine of the Frost Giants",
    "Gate of the Dead",
    "Takamagahara Shrine",
    "Heaven's Labyrinth",

    "Valhalla",
    "Dark Star Lord's Mausoleum",
    "Ancient Chaos",
    "Hall of Malice",
    "Eternal Prison",
    "Eternal Prison - Gloom",
    "Nibiru",
    "Spiral Hell",

    "Altar",
    "Cavern",
    "Cliff",
    "Corridor of Blood",
    "The Tower of Oannes",

    "Gate of Guidance",
    "Mausoleum of the Giants",
    "Endless Corridor",
    "Gate of Illusion",
    "Inferno Cavern",
]

# (group name, [row names]) in display order. Row names carry the field and map
# coordinate where the wiki gives one, because "which orb am I missing and
# where" is the question these lists exist to answer -- the in-game menu already
# tells you the count.
CHECKLIST = [
    ("Guardians", [
        "Fafnir — Roots of Yggdrasil",
        "Vritra — Valhalla",
        "Kujata — Annwfn",
        "Aten-Ra — Dark Star Lord's Mausoleum",
        "Jormungand — Immortal Battlefield",
        "Anu — Ancient Chaos",
        "Surtr — Icefire Treetop",
        "Echidna — Hall of Malice",
        "Hel — Eternal Prison",
        "9th Child — Spiral Hell",
    ]),
    ("Sacred Orbs", [
        "Village of Departure (G-3)",
        "Roots of Yggdrasil (E-4)",
        "Annwfn (E-5)",
        "Immortal Battlefield (F-6)",
        "Icefire Treetop (F-4)",
        "Divine Fortress (B-3)",
        "Shrine of the Frost Giants (C-2)",
        "Gate of the Dead (B-4)",
        "Takamagahara Shrine (D-5)",
        "Heaven's Labyrinth (C-2)",
    ]),
    ("Mantras", [
        "Himinn — Divine Fortress (D-5)",
        "Iorð — Annwfn (D-4)",
        "Sól — Cavern (B-1)",
        "Máni — Immortal Battlefield (E-7)",
        "Sær — Shrine of the Frost Giants (C-3)",
        "Eldr — Valhalla (D-1)",
        "Vindr — Ancient Chaos (D-5)",
        "Móðir — Eternal Prison - Gloom (C-5)",
        "Barn — Inferno Cavern (A-1)",
        "Nótt — Nibiru (B-2)",
    ]),
    ("Maps", [
        # The wiki's map list labels the Eternal Prison's Doom and Gloom
        # halves separately (below), which is why those two rows don't match
        # the single "Eternal Prison" entry in AREAS -- not a typo.
        "Village of Departure / La-Mulana Ruins — from Nebur or Xelpud",
        "Roots of Yggdrasil (E-3)",
        "Annwfn (E-3)",
        "Immortal Battlefield (F-2)",
        "Icefire Treetop (B-2)",
        "Divine Fortress (D-4)",
        "Shrine of the Frost Giants (D-5)",
        "Gate of the Dead (D-3)",
        "Takamagahara Shrine (D-2)",
        "Heaven's Labyrinth (C-5)",
        "Valhalla (A-3)",
        "Dark Star Lord's Mausoleum (C-6)",
        "Ancient Chaos (B-6)",
        "Hall of Malice (C-1)",
        "Eternal Prison - Doom (D-5)",
        "Eternal Prison - Gloom (E-2)",
    ]),
    ("Apps", [
        "Xelputter",
        "Yagoo Map Reader",
        "Yagoo Map Street",
        "TextTrax 2",
        "Ruins Encyclopedia",
        "Mantra",
        "Guild",
        "Kosugi Research Papers",
        "Enga Musica",
        "Beo Eg-Lana",
        "Alert",
        "Snapshots",
        "Skull",
        "Race Scanner",
        "Death Village",
        "Rose and Camellia",
        "Space Capstar II",
        "Lonely House Moving",
        "Mekuri Master",
        "Bounce Shot",
        "Miracle Witch",
        "Future Development Company",
        "La-Mulana",
        "La-Mulana 2",
    ]),
]
```

Note: `lamulana/__init__.py` imports `.api`, which does not exist yet. Create
`lamulana/api.py` as a bare blueprint for now, so the seed test can import the
package and registering it in `server.py` serves clean 404s rather than
failing on import:

```python
"""HTTP surface for the La-Mulana 2 tracker — routes land here in Task 3."""

from flask import Blueprint

bp = Blueprint("lamulana", __name__, url_prefix="/lamulana")
```

Task 3 fills this in with the real routes.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_lamulana_db.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add lamulana/__init__.py lamulana/seed.py lamulana/api.py tests/test_lamulana_db.py
git commit -m "feat(lamulana): canonical area and checklist data from the wiki"
```

---

## Task 2: Schema and connection lifecycle

**Files:**
- Create: `lamulana/db.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_lamulana_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/conftest.py`, after the `recipes_db` fixture:

```python
@pytest.fixture
def lamulana_db(tmp_path):
    """A fresh, seeded lamulana database on disk, isolated per test.

    Same reasoning as `recipes_db` above: import normally so this module object
    is the one the test file's own `import lamulana.db` also resolves to, and
    get isolation from a per-test database file instead.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    db = importlib.import_module("lamulana.db")
    conn = db.connect(str(tmp_path / "lamulana.db"))
    db.init_schema(conn)
    db.seed_all(conn)
    yield conn
    conn.close()
```

Add to `tests/test_lamulana_db.py`:

```python
import sqlite3
import threading

import pytest

import lamulana.db as db


def test_schema_creates_every_table(lamulana_db):
    names = {r[0] for r in lamulana_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"area", "clue", "thread", "clue_thread", "checklist_item", "meta"} <= names


def test_init_schema_is_rerunnable(lamulana_db):
    db.init_schema(lamulana_db)          # must not raise
    db.init_schema(lamulana_db)
    assert lamulana_db.execute("SELECT COUNT(*) FROM area").fetchone()[0] == 28


def test_seeding_twice_does_not_duplicate(lamulana_db):
    before = lamulana_db.execute("SELECT COUNT(*) FROM checklist_item").fetchone()[0]
    db.seed_all(lamulana_db)
    after = lamulana_db.execute("SELECT COUNT(*) FROM checklist_item").fetchone()[0]
    assert before == after == 70          # 10 + 10 + 10 + 16 + 24


def test_reseeding_preserves_progress(lamulana_db):
    lamulana_db.execute(
        "UPDATE checklist_item SET done = 1, done_at = 123, note = 'behind the ice'"
        " WHERE name LIKE 'Vritra%'"
    )
    lamulana_db.commit()
    db.seed_all(lamulana_db)
    row = lamulana_db.execute(
        "SELECT done, done_at, note FROM checklist_item WHERE name LIKE 'Vritra%'"
    ).fetchone()
    assert (row["done"], row["done_at"], row["note"]) == (1, 123, "behind the ice")


def test_foreign_keys_are_on(lamulana_db):
    assert lamulana_db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_deleting_a_clue_removes_its_links(lamulana_db):
    lamulana_db.execute(
        "INSERT INTO clue (id, title, created_at, updated_at) VALUES (1, 'x', 0, 0)")
    lamulana_db.execute(
        "INSERT INTO thread (id, title, created_at, updated_at) VALUES (1, 'y', 0, 0)")
    lamulana_db.execute("INSERT INTO clue_thread (clue_id, thread_id) VALUES (1, 1)")
    lamulana_db.commit()
    lamulana_db.execute("DELETE FROM clue WHERE id = 1")
    lamulana_db.commit()
    assert lamulana_db.execute("SELECT COUNT(*) FROM clue_thread").fetchone()[0] == 0


def test_schema_version_is_recorded(lamulana_db):
    row = lamulana_db.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    assert int(row["value"]) == db.SCHEMA_VERSION


def test_checklist_uniqueness_is_case_insensitive(lamulana_db):
    """The COLLATE NOCASE unique constraint is load-bearing, not decorative."""
    lamulana_db.execute(
        "DELETE FROM checklist_item"
        " WHERE group_name = 'Guardians' AND name = 'Fafnir — Roots of Yggdrasil'")
    lamulana_db.execute(
        "INSERT INTO checklist_item (group_name, name, position)"
        " VALUES ('Guardians', 'fafnir — roots of yggdrasil', 99)")
    lamulana_db.commit()
    db.seed_checklist(lamulana_db)
    rows = lamulana_db.execute(
        "SELECT COUNT(*) FROM checklist_item WHERE group_name = 'Guardians'"
    ).fetchone()[0]
    assert rows == 10          # the hand-inserted row merged, not a duplicate


def test_reseeding_with_reordered_checklist_moves_position_and_keeps_progress(
        lamulana_db, monkeypatch):
    """The other half of seed_checklist's contract: position tracks seed.py,
    `done`/`note` do not -- see test_reseeding_preserves_progress above for the
    first half."""
    lamulana_db.execute(
        "UPDATE checklist_item SET done = 1, note = 'nice'"
        " WHERE group_name = 'Guardians' AND name = 'Fafnir — Roots of Yggdrasil'")
    lamulana_db.commit()
    reordered = [
        (group, list(reversed(items)) if group == "Guardians" else items)
        for group, items in seed.CHECKLIST
    ]
    monkeypatch.setattr(db, "CHECKLIST", reordered)

    db.seed_checklist(lamulana_db)

    row = lamulana_db.execute(
        "SELECT position, done, note FROM checklist_item"
        " WHERE group_name = 'Guardians' AND name = 'Fafnir — Roots of Yggdrasil'"
    ).fetchone()
    assert row["position"] == 9          # was first, reversed puts it last
    assert (row["done"], row["note"]) == (1, "nice")


def test_reseeding_with_reordered_areas_moves_position_but_keeps_id(
        lamulana_db, monkeypatch):
    before_id = lamulana_db.execute(
        "SELECT id FROM area WHERE name = 'Village of Departure'").fetchone()["id"]
    monkeypatch.setattr(db, "AREAS", list(reversed(seed.AREAS)))

    db.seed_areas(lamulana_db)

    row = lamulana_db.execute(
        "SELECT id, position FROM area WHERE name = 'Village of Departure'"
    ).fetchone()
    assert row["id"] == before_id        # same row, not delete-and-reinsert
    assert row["position"] == len(seed.AREAS) - 1


def test_ordered_migration_steps_run_once_in_order(lamulana_db, monkeypatch):
    """MIGRATIONS is empty today, so exercise the machinery with fake steps."""
    ran = []
    steps = [("first", lambda c: ran.append("first")),
             ("second", lambda c: ran.append("second"))]
    monkeypatch.setattr(db, "MIGRATIONS", steps)
    monkeypatch.setattr(db, "SCHEMA_VERSION", len(steps))

    db.init_schema(lamulana_db)
    assert ran == ["first", "second"]

    db.init_schema(lamulana_db)
    assert ran == ["first", "second"], "already-applied steps must not re-run"
    assert db._schema_version(lamulana_db) == 2


def test_clue_state_outside_the_vocabulary_is_rejected(lamulana_db):
    """The CHECK constraint is storage-level enforcement, not just app-level."""
    with pytest.raises(sqlite3.IntegrityError):
        lamulana_db.execute(
            "INSERT INTO clue (title, state, created_at, updated_at)"
            " VALUES ('x', 'bogus', 0, 0)")


def test_migration_does_not_deadlock_on_the_write_lock(tmp_path):
    """`LOCK` is not reentrant; init_schema -> migrate must not nest it.

    Run on a worker thread with a join timeout so a nested acquire fails this
    test instead of hanging the whole suite -- a deadlock's signature is work
    that never finishes, which a plain call here could not distinguish.
    """
    conn = db.connect(str(tmp_path / "deadlock.db"))
    done = []

    worker = threading.Thread(target=lambda: done.append(db.init_schema(conn)))
    worker.daemon = True
    worker.start()
    worker.join(timeout=20)

    assert not worker.is_alive(), "init_schema deadlocked on the non-reentrant LOCK"
    assert done == [None], "worker thread never completed init_schema"
    assert not db.LOCK.locked(), "LOCK must be released after migrating"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_lamulana_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lamulana.db'`

- [ ] **Step 3: Write the database module**

Create `lamulana/db.py`:

```python
"""Schema, connection lifecycle, and seeding for the La-Mulana 2 tracker.

Callers pass a path and drive the lifecycle themselves, exactly as
`recipes/db.py` does. Long-lived server use goes through `get_conn()`, which
hands each request thread its own connection -- see that function for why one
shared connection is not an option.

Schema changes go through `MIGRATIONS`. This is deliberately simpler than the
column-reflection engine in `recipes/db.py`: that exists because recipes already
had databases deployed in the house when columns started being added to it. No
lamulana.db exists anywhere yet, so every change from here is written with full
knowledge of what is on disk. If that ever stops being true, extract recipes'
engine into a module both packages import rather than copying it.
"""

import sqlite3
import threading

from .seed import AREAS, CHECKLIST

LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS area (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL
);

-- state: 'raw' (copied down, meaning unknown), 'understood' (you know what it
-- says and cannot act on it yet), 'used' (spent). The middle one is the whole
-- point of the app, so it is a column and not a tag.
CREATE TABLE IF NOT EXISTS clue (
    id             INTEGER PRIMARY KEY,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL DEFAULT '',
    area_id        INTEGER REFERENCES area(id) ON DELETE SET NULL,
    room           TEXT,
    source         TEXT NOT NULL DEFAULT 'tablet'
                   CHECK (source IN ('tablet', 'npc', 'mail', 'other')),
    interpretation TEXT,
    state          TEXT NOT NULL DEFAULT 'raw'
                   CHECK (state IN ('raw', 'understood', 'used')),
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS thread (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    area_id    INTEGER REFERENCES area(id) ON DELETE SET NULL,
    body       TEXT,
    state      TEXT NOT NULL DEFAULT 'open'
               CHECK (state IN ('open', 'solved')),
    solution   TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    solved_at  INTEGER
);

CREATE TABLE IF NOT EXISTS clue_thread (
    clue_id   INTEGER NOT NULL REFERENCES clue(id) ON DELETE CASCADE,
    thread_id INTEGER NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
    PRIMARY KEY (clue_id, thread_id)
);

-- COLLATE NOCASE on the uniqueness: a user-added row that differs from a seeded
-- one only in case is the same row, and letting both exist would mean the seed
-- silently stops being idempotent the first time someone types lowercase.
CREATE TABLE IF NOT EXISTS checklist_item (
    id         INTEGER PRIMARY KEY,
    group_name TEXT NOT NULL,
    name       TEXT NOT NULL,
    position   INTEGER NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    done_at    INTEGER,
    note       TEXT,
    UNIQUE (group_name, name COLLATE NOCASE)
);

CREATE INDEX IF NOT EXISTS idx_clue_area   ON clue(area_id);
CREATE INDEX IF NOT EXISTS idx_clue_state  ON clue(state);
CREATE INDEX IF NOT EXISTS idx_thread_state ON thread(state);
"""

# Ordered, append-only. Position in this list IS the version number recorded in
# meta.schema_version -- never reorder or delete. Each step must be idempotent
# and must guard on what the database actually looks like (PRAGMA table_info,
# sqlite_master), not on the version number.
#
# Empty at first release, and that is the intended state: CREATE TABLE IF NOT
# EXISTS covers a database that does not exist yet, which is every database
# today.
MIGRATIONS = []          # list of (name, callable taking a connection)

SCHEMA_VERSION = len(MIGRATIONS)


def connect(path):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Per-thread connection cache, on the module object deliberately -- see
# recipes/db.py for the reload history that made that necessary there. Keyed
# by path here for the same reason it is there: even a cache entry that
# somehow outlived a reload could never be handed back for a different
# database file. Connections are never closed; the count is bounded by the
# request thread pool and, outside tests, by the one path this process ever
# opens.
_LOCAL = threading.local()


def get_conn(path):
    """The calling thread's connection to `path`, opened on first use.

    One sqlite3.Connection shared across waitress's thread pool is a measured
    bug, not a theoretical one: a connection caches prepared statements by SQL
    text, so two threads running the same query get the same statement object,
    and whichever resets it first leaves the other reading a short row. WAL mode
    plus a connection each is the fix; writers are serialised by LOCK.
    """
    cache = getattr(_LOCAL, "conns", None)
    if cache is None:
        cache = _LOCAL.conns = {}
    conn = cache.get(path)
    if conn is None:
        conn = cache[path] = connect(path)
    return conn


def _schema_version(conn):
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 0


def migrate(conn):
    """Run any MIGRATIONS steps this database has not seen, then record where it is."""
    with LOCK:
        start = _schema_version(conn)
        for _name, step in MIGRATIONS[start:]:
            step(conn)
        # Never stamp downwards. Rolling a deploy back leaves a database ahead
        # of the code reading it; recording the older number would claim
        # migrations had been undone when they haven't, and re-run them on the
        # next deploy forward.
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(max(start, SCHEMA_VERSION)),),
        )
        conn.commit()


def init_schema(conn):
    with LOCK:
        conn.executescript(SCHEMA)
        conn.commit()
    # Outside the block above on purpose: LOCK is a plain, non-reentrant
    # threading.Lock and migrate() takes it itself.
    migrate(conn)


def seed_areas(conn):
    """Insert seeded areas, keyed by name so `area.id` never moves under a clue.

    The ON CONFLICT clause updates `position` only, so reordering AREAS in
    seed.py reorders the display without changing the `id` that `clue.area_id`
    and `thread.area_id` reference -- `INSERT OR IGNORE` would fail that the
    moment an existing name came back around, since IGNORE also skips the
    position update. Renaming or deleting an AREAS entry is not handled here:
    the old row is left in place rather than pruned, same as
    `seed_checklist` below -- pruning on a rename you didn't intend would
    silently detach clues and threads from the area they're filed under.
    """
    with LOCK:
        for position, name in enumerate(AREAS):
            conn.execute(
                "INSERT INTO area (name, position) VALUES (?, ?)"
                " ON CONFLICT(name) DO UPDATE SET position = excluded.position",
                (name, position),
            )
        conn.commit()


def seed_checklist(conn):
    """Insert seeded rows, never touching `done`, `done_at`, or `note`.

    Re-running this after adding rows to seed.py is a normal thing to do, and it
    must never cost the player a tick they earned. The ON CONFLICT clause
    updates `position` only, so reordering seed.py reorders the display without
    disturbing progress. Renaming or deleting a CHECKLIST row is not handled
    here either: the old row is left in place -- an orphan -- rather than
    pruned, deliberately, because pruning a row on a rename you didn't intend
    would silently destroy whatever `done`/`note` progress it carried.
    """
    with LOCK:
        for group_name, items in CHECKLIST:
            for position, name in enumerate(items):
                conn.execute(
                    "INSERT INTO checklist_item (group_name, name, position)"
                    " VALUES (?, ?, ?)"
                    " ON CONFLICT(group_name, name) DO UPDATE SET"
                    " position = excluded.position",
                    (group_name, name, position),
                )
        conn.commit()


def seed_all(conn):
    seed_areas(conn)
    seed_checklist(conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_lamulana_db.py -v`
Expected: PASS, 18 passed

- [ ] **Step 5: Check nothing else broke**

Run: `.venv/bin/python -m pytest -q`
Expected: all existing tests still pass (the conftest change added a fixture, it
did not alter an existing one)

- [ ] **Step 6: Commit**

```bash
git add lamulana/db.py tests/conftest.py tests/test_lamulana_db.py
git commit -m "feat(lamulana): schema, per-thread connections, idempotent seed"
```

---

## Task 3: Blueprint skeleton, page route, bootstrap

**Files:**
- Create: `auth.py` (the write-gate predicate, shared by the soundboard and every blueprint)
- Rewrite: `lamulana/api.py`
- Modify: `server.py` (defensive blueprint mount; `can_edit`/`need_edit` now import from `auth.py`)
- Modify: `recipes/api.py` (`can_edit`/`need_edit` now import from `auth.py`)
- Modify: `tests/conftest.py` (sys.modules scrub)
- Create: `templates/lamulana.html` (placeholder, replaced in Task 9)
- Test: `tests/test_lamulana_api.py`
- Test: `tests/test_lamulana_db.py` (one CHECK-constraint test, added alongside this task)

Two things ride along with the blueprint mount here, both caught in review
rather than planned up front: `can_edit`/`need_edit` had drifted into three
near-identical copies (`server.py`, `recipes/api.py`, this file), so they move
to a shared `auth.py`; and a broken `lamulana.db` must not be able to take the
soundboard down with it, so the mount is defensive.

- [ ] **Step 1: Write the failing test**

Create `tests/test_lamulana_api.py`:

```python
import importlib
import os
import pathlib
import stat
import sys


def test_page_renders(client):
    r = client.get("/lamulana/")
    assert r.status_code == 200


def test_bootstrap_checklist_keeps_the_seeds_group_order(client):
    data = client.get("/lamulana/api/bootstrap").get_json()
    assert [g["group"] for g in data["checklist"]] == [
        "Guardians", "Sacred Orbs", "Mantras", "Maps", "Apps"]
    guardians = data["checklist"][0]["items"]
    assert guardians[0]["name"].startswith("Fafnir")     # position order, not id
    assert "group_name" not in guardians[0]              # folded into the wrapper
    assert guardians[0]["done"] is False                 # a JSON bool, not 0


def test_bootstrap_counts_distinguish_their_sources(client, app):
    conn = sys.modules["lamulana.api"]._conn()
    conn.execute("INSERT INTO clue (title, state, created_at, updated_at)"
                 " VALUES ('a', 'raw', 0, 0)")
    conn.execute("INSERT INTO clue (title, state, created_at, updated_at)"
                 " VALUES ('b', 'understood', 0, 0)")
    conn.execute("INSERT INTO thread (title, state, created_at, updated_at)"
                 " VALUES ('t', 'open', 0, 0)")
    conn.execute("INSERT INTO thread (title, state, created_at, updated_at)"
                 " VALUES ('u', 'solved', 0, 0)")
    conn.commit()
    counts = client.get("/lamulana/api/bootstrap").get_json()["counts"]
    assert counts == {"clues": 2, "clues_understood": 1, "threads_open": 1}


def test_soundboard_survives_a_broken_tracker_database(tmp_path, monkeypatch):
    """A lamulana.db that fails to open must cost /lamulana, not the app.

    server.py wraps the blueprint mount in try/except for exactly this: an
    unwritable or corrupt tracker database must not take the soundboard and
    the recipes list down with it. Simulate "can't open" the same way SQLite
    hits it -- a database file with no permissions -- rather than an
    unwritable directory, which would also break the recipes blueprint's own
    database and defeat the point of the test.
    """
    data = tmp_path / "data"
    sounds = tmp_path / "sounds"
    data.mkdir()
    sounds.mkdir()

    broken = data / "lamulana.db"
    broken.write_bytes(b"")
    os.chmod(broken, 0)

    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SOUND_DIR", str(sounds))
    monkeypatch.setenv("USER_PASS", "editpw")
    monkeypatch.setenv("ADMIN_PASS", "adminpw")
    monkeypatch.setenv("CATALOG_SEED", str(tmp_path / "nonexistent.db"))

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    if "server" in sys.modules:
        del sys.modules["server"]
    for mod in [m for m in sys.modules
                if m in ("recipes", "lamulana")
                or m.startswith(("recipes.", "lamulana."))]:
        del sys.modules[mod]

    try:
        server = importlib.import_module("server")
        server.scan_library()
        server.app.config["TESTING"] = True
        c = server.app.test_client()

        assert c.get("/api/sounds").status_code == 200
        assert c.get("/lamulana/").status_code == 404
    finally:
        os.chmod(broken, stat.S_IRUSR | stat.S_IWUSR)  # let tmp_path cleanup remove it
        if "server" in sys.modules:
            del sys.modules["server"]
        for mod in [m for m in sys.modules
                    if m in ("recipes", "lamulana")
                    or m.startswith(("recipes.", "lamulana."))]:
            del sys.modules[mod]
```

Also add to `tests/test_lamulana_db.py`, after `test_ordered_migration_steps_run_once_in_order`
(needs `import sqlite3` and `import pytest` at the top of that file, added in Task 2):

```python
def test_clue_state_outside_the_vocabulary_is_rejected(lamulana_db):
    """The CHECK constraint is storage-level enforcement, not just app-level."""
    with pytest.raises(sqlite3.IntegrityError):
        lamulana_db.execute(
            "INSERT INTO clue (title, state, created_at, updated_at)"
            " VALUES ('x', 'bogus', 0, 0)")
```

The two bootstrap tests are deliberately load-bearing, not smoke tests: the
checklist one fails if `_checklist_groups()` stops popping `group_name` or
stops coercing `done` to a real bool, and the counts one uses distinguishable
values (2/1/1) precisely so a count pointed at the wrong table or the wrong
state string doesn't accidentally still match.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_lamulana_api.py -v`
Expected: FAIL — 404 on `/lamulana/`

- [ ] **Step 3: Create the shared write-gate predicate**

Create `auth.py` at the repo root:

```python
"""Who may write. One definition, imported by the soundboard and every blueprint.

Deliberately not three copies: this is the predicate that decides whether a
request may change state, and a copy that drifts from the others is a hole
rather than an inconsistency. Listening and reading stay open to everyone --
these gate writes only.
"""
from flask import jsonify, session


def can_edit():
    return bool(session.get("admin") or session.get("can_edit"))


def need_edit():
    """An error response if this session may not write, else None."""
    if not can_edit():
        return jsonify({"error": "login required"}), 403
    return None
```

`auth.py` imports only `flask`, so there is no circular import — blueprints
can import from the repo root freely; `server.py` is the one that imports the
blueprints, never the reverse.

- [ ] **Step 4: Write the blueprint**

Replace `lamulana/api.py` entirely:

```python
"""HTTP surface for the La-Mulana 2 tracker.

Read it in order: helpers, then clues, then threads, then the link between them,
then search and the checklist. The thread routes are the ones with real behavior
-- solving a thread reaches back into the clues that fed it.

Only the page route and bootstrap exist so far. Clues, threads, the link
between them, and search land in Tasks 4-8 -- if you came here looking for
them, they are not written yet.
"""

import os
import time

from flask import Blueprint, jsonify, render_template, request

from auth import can_edit, need_edit

from . import db as _db
from .seed import CHECKLIST as _SEED_CHECKLIST

bp = Blueprint("lamulana", __name__, url_prefix="/lamulana")

DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "lamulana.db")

# Used by Task 4 to validate request bodies for the clue/thread routes it adds.
CLUE_STATES = ("raw", "understood", "used")
CLUE_SOURCES = ("tablet", "npc", "mail", "other")
THREAD_STATES = ("open", "solved")


def _conn():
    """This request thread's connection. A dict lookup on a threading.local."""
    return _db.get_conn(DB_PATH)


# Schema and seed run once here at import, on the importing thread, not
# per-thread inside _conn(). Both are idempotent, but running them on every
# thread's first request would mean a burst of redundant writes contending for
# the write lock during the first seconds after a deploy.
_db.init_schema(_conn())
_db.seed_all(_conn())


def _body():
    return request.get_json(silent=True) or {}


def _now():
    return int(time.time())


@bp.route("/")
def page():
    return render_template("lamulana.html")


@bp.route("/api/bootstrap")
def api_bootstrap():
    """Everything the page needs on load, in one request."""
    areas = [dict(r) for r in _conn().execute(
        "SELECT id, name, position FROM area ORDER BY position"
    ).fetchall()]
    counts = {
        "clues": _conn().execute("SELECT COUNT(*) FROM clue").fetchone()[0],
        "clues_understood": _conn().execute(
            "SELECT COUNT(*) FROM clue WHERE state = 'understood'").fetchone()[0],
        "threads_open": _conn().execute(
            "SELECT COUNT(*) FROM thread WHERE state = 'open'").fetchone()[0],
    }
    return jsonify({"areas": areas, "checklist": _checklist_groups(), "counts": counts})


# Group name -> its index in seed.CHECKLIST, i.e. the authored progression
# order (Guardians, Sacred Orbs, Mantras, Maps, Apps) that the Progress screen
# renders in. checklist_item has no group-position column of its own -- there
# is no schema change worth a migration for a database nothing has written to
# yet, per lamulana/db.py's docstring -- so the order is imposed here in
# Python instead of in SQL.
_GROUP_RANK = {name: i for i, (name, _items) in enumerate(_SEED_CHECKLIST)}


def _checklist_groups():
    # ORDER BY group_name here is only the within-group tiebreak's foundation
    # (position, id); the group_name term just keeps rows for the same group
    # adjacent so the loop below can bucket them in one pass. The group order
    # itself is re-imposed by _GROUP_RANK after grouping, below.
    rows = _conn().execute(
        "SELECT id, group_name, name, position, done, done_at, note"
        " FROM checklist_item ORDER BY group_name, position, id"
    ).fetchall()
    by_group = {}
    for r in rows:
        item = dict(r)
        item.pop("group_name")
        item["done"] = bool(item["done"])
        by_group.setdefault(r["group_name"], []).append(item)
    # Seeded groups sort by their authored progression order; a group a player
    # added that isn't in the seed sorts alphabetically after all of them.
    groups = sorted(by_group, key=lambda g: (_GROUP_RANK.get(g, len(_GROUP_RANK)), g))
    return [{"group": g, "items": by_group[g]} for g in groups]
```

Note `_checklist_groups()` orders top-level groups by `_GROUP_RANK`, not by
`group_name` alphabetically — `group_name` in the SQL `ORDER BY` is only there
to make the single-pass bucketing loop simple, and would otherwise put "Apps"
first, which is wrong: it's the last thing you unlock, not the first.

- [ ] **Step 5: Point `server.py` and `recipes/api.py` at the shared predicate**

In `server.py`, delete the local `can_edit()` definition and import it instead:

```python
from auth import can_edit
```

(add this import near the top, alongside the other non-stdlib imports; `session`
stays imported from `flask` there, since `server.py` still uses it directly
elsewhere).

In `recipes/api.py`, delete the local `can_edit()` and `need_edit()` definitions
and import both instead:

```python
from auth import can_edit, need_edit
```

`session` drops out of `recipes/api.py`'s `flask` import — nothing else in that
file uses it directly.

- [ ] **Step 6: Mount the blueprint defensively**

In `server.py`, find the existing two lines (near line 61):

```python
from recipes import recipes_bp
app.register_blueprint(recipes_bp)
```

and make them:

```python
from recipes import recipes_bp
app.register_blueprint(recipes_bp)

# The tracker owns its own database and bootstraps it at import. Mount it
# defensively: an unwritable or corrupt lamulana.db must cost us /lamulana,
# not the soundboard and the recipes list in the same process.
try:
    from lamulana import lamulana_bp
    app.register_blueprint(lamulana_bp)
except Exception:
    traceback.print_exc()
```

`server.py` does not import `traceback` yet — add `import traceback` alongside
its other stdlib imports.

- [ ] **Step 7: Scrub the module in tests**

In `tests/conftest.py`, find the loop that deletes cached `recipes` modules and
extend it to cover `lamulana`, which caches `DB_PATH` at import for the same
reason:

```python
    for mod in [m for m in sys.modules
                if m in ("recipes", "lamulana")
                or m.startswith(("recipes.", "lamulana."))]:
        del sys.modules[mod]
```

Update the comment above the loop too — it explained the scrub purely in terms
of `recipes`; say `lamulana/api.py` resolves `DB_PATH` at import for the same
reason.

- [ ] **Step 8: Add a placeholder template**

Create `templates/lamulana.html` with a single line, replaced wholesale in Task 9:

```html
<!doctype html><title>La-Mulana 2</title><p>placeholder</p>
```

- [ ] **Step 9: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_lamulana_api.py tests/test_lamulana_db.py -v`
Expected: PASS, 22 passed (4 in test_lamulana_api.py, 18 in test_lamulana_db.py)

Then deliberately break `_checklist_groups()` (drop the `group_name` pop and
the `bool()` coercion) and confirm
`test_bootstrap_checklist_keeps_the_seeds_group_order` fails, to prove the test
is load-bearing before trusting it. Restore afterward.

- [ ] **Step 10: Check nothing else broke**

Run: `.venv/bin/python -m pytest -q`
Expected: all existing tests still pass — 281 passed (279 before this task's
test changes, +1 for the CHECK-constraint test in Task 2's file, +1 net new in
this task's file: two bootstrap tests were replaced 1-for-1 and one new
soundboard-resilience test was added).

- [ ] **Step 11: Commit**

```bash
git add auth.py lamulana/api.py lamulana/db.py server.py recipes/api.py \
        tests/conftest.py tests/test_lamulana_api.py tests/test_lamulana_db.py \
        templates/lamulana.html
git commit -m "feat(lamulana): mount the blueprint, serve bootstrap"
```

---

## Task 4: Clue CRUD

**Files:**
- Modify: `lamulana/api.py`
- Test: `tests/test_lamulana_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_lamulana_api.py`:

```python
def _area_id(client, name):
    for a in client.get("/lamulana/api/bootstrap").get_json()["areas"]:
        if a["name"] == name:
            return a["id"]
    raise AssertionError(f"no area named {name}")


def test_create_clue_returns_it_in_full(editor_client):
    area = _area_id(editor_client, "Annwfn")
    r = editor_client.post("/lamulana/api/clues", json={
        "title": "twin serpents",
        "body": "Where the twin serpents meet, the child sleeps.",
        "area_id": area,
        "room": "E-3",
    })
    assert r.status_code == 200
    clue = r.get_json()["clue"]
    assert clue["title"] == "twin serpents"
    assert clue["area"] == "Annwfn"
    assert clue["state"] == "raw"
    assert clue["source"] == "tablet"
    assert clue["threads"] == []
    assert clue["id"] > 0


def test_clue_state_moves_through_the_lifecycle(editor_client):
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a"}
                              ).get_json()["clue"]["id"]
    r = editor_client.patch(f"/lamulana/api/clues/{cid}",
                             json={"state": "understood",
                                   "interpretation": "means the ankh in Valhalla"})
    assert r.get_json()["clue"]["state"] == "understood"
    assert r.get_json()["clue"]["interpretation"] == "means the ankh in Valhalla"
    r = editor_client.patch(f"/lamulana/api/clues/{cid}", json={"state": "used"})
    assert r.get_json()["clue"]["state"] == "used"


def test_bad_state_is_rejected(editor_client):
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a"}
                              ).get_json()["clue"]["id"]
    r = editor_client.patch(f"/lamulana/api/clues/{cid}", json={"state": "solved"})
    assert r.status_code == 400


def test_clue_requires_a_title(editor_client):
    assert editor_client.post("/lamulana/api/clues", json={"body": "x"}).status_code == 400


def test_clues_filter_by_area_and_state(editor_client):
    ann = _area_id(editor_client, "Annwfn")
    val = _area_id(editor_client, "Valhalla")
    editor_client.post("/lamulana/api/clues", json={"title": "a", "area_id": ann})
    editor_client.post("/lamulana/api/clues", json={"title": "b", "area_id": val,
                                                     "state": "understood"})
    got = editor_client.get(f"/lamulana/api/clues?area={ann}").get_json()["clues"]
    assert [c["title"] for c in got] == ["a"]
    got = editor_client.get("/lamulana/api/clues?state=understood").get_json()["clues"]
    assert [c["title"] for c in got] == ["b"]


def test_delete_clue(editor_client):
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a"}
                              ).get_json()["clue"]["id"]
    assert editor_client.delete(f"/lamulana/api/clues/{cid}").status_code == 200
    assert editor_client.get("/lamulana/api/clues").get_json()["clues"] == []


def test_clue_writes_need_an_editing_session(reader_client):
    assert reader_client.post("/lamulana/api/clues", json={"title": "a"}).status_code == 403
    assert reader_client.patch("/lamulana/api/clues/1", json={}).status_code == 403
    assert reader_client.delete("/lamulana/api/clues/1").status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_lamulana_api.py -v`
Expected: FAIL — 404 on `/lamulana/api/clues`

- [ ] **Step 3: Implement the clue routes**

Append to `lamulana/api.py`:

```python
# ---------------------------------------------------------------------------
# Clues
# ---------------------------------------------------------------------------

CLUE_SELECT = """
    SELECT c.id, c.title, c.body, c.area_id, a.name AS area, c.room, c.source,
           c.interpretation, c.state, c.created_at, c.updated_at
    FROM clue c LEFT JOIN area a ON a.id = c.area_id
"""


def _search_terms(q, columns):
    """(sql_clause, params) ANDing every word in `q` across `columns`.

    Plain LIKE rather than FTS5: a playthrough produces a few hundred rows, so
    the scan is instant, it does not depend on how the host's SQLite was
    compiled, and there is no index to drift out of sync with the table.
    """
    words = [w for w in (q or "").split() if w]
    if not words:
        return "", []
    blob = " || ' ' || ".join(f"COALESCE({c}, '')" for c in columns)
    clause = " AND ".join([f"{blob} LIKE ?"] * len(words))
    return clause, [f"%{w}%" for w in words]


def _threads_for_clues(ids):
    """{clue_id: [{id, title, state}]} for the given clue ids, in one query."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = _conn().execute(f"""
        SELECT ct.clue_id, t.id, t.title, t.state
        FROM clue_thread ct JOIN thread t ON t.id = ct.thread_id
        WHERE ct.clue_id IN ({marks})
        ORDER BY t.title
    """, ids).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["clue_id"], []).append(
            {"id": r["id"], "title": r["title"], "state": r["state"]})
    return out


def _clue_json(rows):
    clues = [dict(r) for r in rows]
    links = _threads_for_clues([c["id"] for c in clues])
    for c in clues:
        c["threads"] = links.get(c["id"], [])
    return clues


def _one_clue(clue_id):
    row = _conn().execute(CLUE_SELECT + " WHERE c.id = ?", (clue_id,)).fetchone()
    return _clue_json([row])[0] if row else None


@bp.route("/api/clues")
def api_clues():
    where, params = [], []
    if request.args.get("area"):
        where.append("c.area_id = ?"); params.append(request.args["area"])
    if request.args.get("state"):
        where.append("c.state = ?"); params.append(request.args["state"])
    if request.args.get("source"):
        where.append("c.source = ?"); params.append(request.args["source"])
    clause, qp = _search_terms(request.args.get("q"),
                               ["c.title", "c.body", "c.interpretation", "c.room"])
    if clause:
        where.append(clause); params += qp
    sql = CLUE_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.updated_at DESC, c.id DESC"
    return jsonify({"clues": _clue_json(_conn().execute(sql, params).fetchall())})


@bp.route("/api/clues", methods=["POST"])
def api_clue_create():
    if (err := need_edit()):
        return err
    b = _body()
    title = (b.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    state = b.get("state") or "raw"
    source = b.get("source") or "tablet"
    if state not in CLUE_STATES or source not in CLUE_SOURCES:
        return jsonify({"error": "bad state or source"}), 400
    now = _now()
    with _db.LOCK:
        cur = _conn().execute("""
            INSERT INTO clue (title, body, area_id, room, source, interpretation,
                              state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, b.get("body") or "", b.get("area_id"), b.get("room"),
              source, b.get("interpretation"), state, now, now))
        _conn().commit()
    return jsonify({"clue": _one_clue(cur.lastrowid)})


CLUE_PATCHABLE = ("title", "body", "area_id", "room", "source",
                  "interpretation", "state")


@bp.route("/api/clues/<int:clue_id>", methods=["PATCH"])
def api_clue_patch(clue_id):
    if (err := need_edit()):
        return err
    b = _body()
    if "state" in b and b["state"] not in CLUE_STATES:
        return jsonify({"error": "bad state"}), 400
    if "source" in b and b["source"] not in CLUE_SOURCES:
        return jsonify({"error": "bad source"}), 400
    if "title" in b and not (b.get("title") or "").strip():
        return jsonify({"error": "title required"}), 400
    fields = [k for k in CLUE_PATCHABLE if k in b]
    if not fields:
        return jsonify({"error": "nothing to change"}), 400
    if not _one_clue(clue_id):
        return jsonify({"error": "no such clue"}), 404
    sets = ", ".join(f"{f} = ?" for f in fields)
    params = [b[f] for f in fields] + [_now(), clue_id]
    with _db.LOCK:
        _conn().execute(f"UPDATE clue SET {sets}, updated_at = ? WHERE id = ?", params)
        _conn().commit()
    return jsonify({"clue": _one_clue(clue_id)})


@bp.route("/api/clues/<int:clue_id>", methods=["DELETE"])
def api_clue_delete(clue_id):
    if (err := need_edit()):
        return err
    with _db.LOCK:
        _conn().execute("DELETE FROM clue WHERE id = ?", (clue_id,))
        _conn().commit()
    return jsonify({"ok": True})


@bp.route("/api/rooms")
def api_rooms():
    """Distinct room names, for the capture form's autocomplete."""
    rows = _conn().execute(
        "SELECT DISTINCT room FROM clue WHERE room IS NOT NULL AND room != ''"
        " ORDER BY room"
    ).fetchall()
    return jsonify({"rooms": [r["room"] for r in rows]})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_lamulana_api.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add lamulana/api.py tests/test_lamulana_api.py
git commit -m "feat(lamulana): clue CRUD, filters, and room autocomplete"
```

---

## Task 5: Thread CRUD and detail

**Files:**
- Modify: `lamulana/api.py`
- Test: `tests/test_lamulana_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_lamulana_api.py`:

```python
def test_create_thread(editor_client):
    area = _area_id(editor_client, "Immortal Battlefield")
    r = editor_client.post("/lamulana/api/threads", json={
        "title": "ankh won't spawn", "area_id": area,
        "body": "solved the block puzzle, no ankh",
    })
    assert r.status_code == 200
    t = r.get_json()["thread"]
    assert t["state"] == "open"
    assert t["area"] == "Immortal Battlefield"
    assert t["clue_count"] == 0


def test_thread_detail_has_no_clues_yet(editor_client):
    tid = editor_client.post("/lamulana/api/threads", json={"title": "t"}
                              ).get_json()["thread"]["id"]
    r = editor_client.get(f"/lamulana/api/threads/{tid}")
    assert r.get_json()["thread"]["clues"] == []


def test_thread_detail_404s_when_missing(editor_client):
    assert editor_client.get("/lamulana/api/threads/9999").status_code == 404


def test_threads_filter_by_state(editor_client):
    editor_client.post("/lamulana/api/threads", json={"title": "open one"})
    tid = editor_client.post("/lamulana/api/threads", json={"title": "done one"}
                              ).get_json()["thread"]["id"]
    editor_client.patch(f"/lamulana/api/threads/{tid}", json={"state": "solved"})
    got = editor_client.get("/lamulana/api/threads?state=open").get_json()["threads"]
    assert [t["title"] for t in got] == ["open one"]


def test_thread_writes_need_an_editing_session(reader_client):
    assert reader_client.post("/lamulana/api/threads", json={"title": "a"}).status_code == 403
    assert reader_client.patch("/lamulana/api/threads/1", json={}).status_code == 403
    assert reader_client.delete("/lamulana/api/threads/1").status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_lamulana_api.py -v`
Expected: FAIL — 404 on `/lamulana/api/threads`

- [ ] **Step 3: Implement the thread routes**

Append to `lamulana/api.py`:

```python
# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------

THREAD_SELECT = """
    SELECT t.id, t.title, t.area_id, a.name AS area, t.body, t.state, t.solution,
           t.created_at, t.updated_at, t.solved_at,
           (SELECT COUNT(*) FROM clue_thread ct WHERE ct.thread_id = t.id)
               AS clue_count
    FROM thread t LEFT JOIN area a ON a.id = t.area_id
"""


def _one_thread(thread_id):
    row = _conn().execute(THREAD_SELECT + " WHERE t.id = ?", (thread_id,)).fetchone()
    return dict(row) if row else None


@bp.route("/api/threads")
def api_threads():
    where, params = [], []
    if request.args.get("area"):
        where.append("t.area_id = ?"); params.append(request.args["area"])
    if request.args.get("state"):
        where.append("t.state = ?"); params.append(request.args["state"])
    clause, qp = _search_terms(request.args.get("q"),
                               ["t.title", "t.body", "t.solution"])
    if clause:
        where.append(clause); params += qp
    sql = THREAD_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Open threads first: this list is a worklist, and a solved thread is
    # history. Within each half, most recently touched first.
    sql += " ORDER BY t.state = 'solved', t.updated_at DESC, t.id DESC"
    return jsonify({"threads": [dict(r) for r in _conn().execute(sql, params)]})


@bp.route("/api/threads/<int:thread_id>")
def api_thread_detail(thread_id):
    """One thread with every linked clue inlined at full length.

    The clues are the point: this is the screen you sit down with when you
    finally try to crack something, and it exists so the scattered text is on
    one page instead of in three browser tabs.
    """
    thread = _one_thread(thread_id)
    if not thread:
        return jsonify({"error": "no such thread"}), 404
    rows = _conn().execute(CLUE_SELECT + """
        JOIN clue_thread ct ON ct.clue_id = c.id
        WHERE ct.thread_id = ?
        ORDER BY c.state, c.id
    """, (thread_id,)).fetchall()
    thread["clues"] = _clue_json(rows)
    return jsonify({"thread": thread})


@bp.route("/api/threads", methods=["POST"])
def api_thread_create():
    if (err := need_edit()):
        return err
    b = _body()
    title = (b.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    now = _now()
    with _db.LOCK:
        cur = _conn().execute("""
            INSERT INTO thread (title, area_id, body, state, created_at, updated_at)
            VALUES (?, ?, ?, 'open', ?, ?)
        """, (title, b.get("area_id"), b.get("body"), now, now))
        _conn().commit()
    return jsonify({"thread": _one_thread(cur.lastrowid)})


THREAD_PATCHABLE = ("title", "area_id", "body", "state", "solution")


@bp.route("/api/threads/<int:thread_id>", methods=["PATCH"])
def api_thread_patch(thread_id):
    if (err := need_edit()):
        return err
    b = _body()
    if "state" in b and b["state"] not in THREAD_STATES:
        return jsonify({"error": "bad state"}), 400
    if "title" in b and not (b.get("title") or "").strip():
        return jsonify({"error": "title required"}), 400
    fields = [k for k in THREAD_PATCHABLE if k in b]
    if not fields:
        return jsonify({"error": "nothing to change"}), 400
    if not _one_thread(thread_id):
        return jsonify({"error": "no such thread"}), 404
    sets = ", ".join(f"{f} = ?" for f in fields)
    params = [b[f] for f in fields] + [_now(), thread_id]
    with _db.LOCK:
        _conn().execute(f"UPDATE thread SET {sets}, updated_at = ? WHERE id = ?", params)
        if b.get("state") == "solved":
            _conn().execute(
                "UPDATE thread SET solved_at = ? WHERE id = ? AND solved_at IS NULL",
                (_now(), thread_id))
        if b.get("state") == "open":
            _conn().execute("UPDATE thread SET solved_at = NULL WHERE id = ?",
                            (thread_id,))
        _conn().commit()
    return jsonify({"thread": _one_thread(thread_id)})


@bp.route("/api/threads/<int:thread_id>", methods=["DELETE"])
def api_thread_delete(thread_id):
    if (err := need_edit()):
        return err
    with _db.LOCK:
        _conn().execute("DELETE FROM thread WHERE id = ?", (thread_id,))
        _conn().commit()
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_lamulana_api.py -v`
Expected: PASS, 15 passed

- [ ] **Step 5: Commit**

```bash
git add lamulana/api.py tests/test_lamulana_api.py
git commit -m "feat(lamulana): thread CRUD and the detail view that inlines clues"
```

---

## Task 6: Linking, and solving a thread

**Files:**
- Modify: `lamulana/api.py`
- Test: `tests/test_lamulana_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_lamulana_api.py`:

```python
def _clue_and_thread(client):
    cid = client.post("/lamulana/api/clues", json={
        "title": "twin serpents", "body": "where the twin serpents meet",
        "state": "understood"}).get_json()["clue"]["id"]
    tid = client.post("/lamulana/api/threads", json={"title": "ankh won't spawn"}
                      ).get_json()["thread"]["id"]
    return cid, tid


def test_link_shows_on_both_sides(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    assert editor_client.post("/lamulana/api/link",
                               json={"clue_id": cid, "thread_id": tid}).status_code == 200
    detail = editor_client.get(f"/lamulana/api/threads/{tid}").get_json()["thread"]
    assert [c["id"] for c in detail["clues"]] == [cid]
    assert detail["clue_count"] == 1
    clue = editor_client.get("/lamulana/api/clues").get_json()["clues"][0]
    assert [t["id"] for t in clue["threads"]] == [tid]


def test_linking_twice_is_harmless(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    r = editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    assert r.status_code == 200
    detail = editor_client.get(f"/lamulana/api/threads/{tid}").get_json()["thread"]
    assert len(detail["clues"]) == 1


def test_unlink(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    assert editor_client.delete("/lamulana/api/link",
                                 json={"clue_id": cid, "thread_id": tid}).status_code == 200
    detail = editor_client.get(f"/lamulana/api/threads/{tid}").get_json()["thread"]
    assert detail["clues"] == []


def test_link_to_a_missing_thread_is_rejected(editor_client):
    cid, _ = _clue_and_thread(editor_client)
    r = editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": 9999})
    assert r.status_code == 404


def test_solving_marks_linked_clues_used(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    r = editor_client.post(f"/lamulana/api/threads/{tid}/solve", json={
        "solution": "incant Sol in front of the tablet", "mark_clues_used": True})
    assert r.status_code == 200
    data = r.get_json()
    assert data["thread"]["state"] == "solved"
    assert data["thread"]["solution"] == "incant Sol in front of the tablet"
    assert data["thread"]["solved_at"] > 0
    assert data["clues_marked"] == 1
    clue = editor_client.get("/lamulana/api/clues").get_json()["clues"][0]
    assert clue["state"] == "used"


def test_solving_can_leave_clues_alone(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    r = editor_client.post(f"/lamulana/api/threads/{tid}/solve", json={
        "solution": "x", "mark_clues_used": False})
    assert r.get_json()["clues_marked"] == 0
    clue = editor_client.get("/lamulana/api/clues").get_json()["clues"][0]
    assert clue["state"] == "understood"


def test_solving_defaults_to_marking_clues_used(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    r = editor_client.post(f"/lamulana/api/threads/{tid}/solve", json={"solution": "x"})
    assert r.get_json()["clues_marked"] == 1


def test_solving_does_not_touch_already_used_clues(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.patch(f"/lamulana/api/clues/{cid}", json={"state": "used"})
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    r = editor_client.post(f"/lamulana/api/threads/{tid}/solve", json={"solution": "x"})
    assert r.get_json()["clues_marked"] == 0


def test_link_and_solve_need_an_editing_session(reader_client):
    assert reader_client.post("/lamulana/api/link", json={}).status_code == 403
    assert reader_client.delete("/lamulana/api/link", json={}).status_code == 403
    assert reader_client.post("/lamulana/api/threads/1/solve", json={}).status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_lamulana_api.py -v`
Expected: FAIL — 404 on `/lamulana/api/link`

- [ ] **Step 3: Implement linking and solving**

Append to `lamulana/api.py`:

```python
# ---------------------------------------------------------------------------
# The link between them
# ---------------------------------------------------------------------------

def _link_pair():
    """(clue_id, thread_id) from the request body, or an error response."""
    b = _body()
    clue_id, thread_id = b.get("clue_id"), b.get("thread_id")
    if not clue_id or not thread_id:
        return None, (jsonify({"error": "clue_id and thread_id required"}), 400)
    if not _one_clue(clue_id):
        return None, (jsonify({"error": "no such clue"}), 404)
    if not _one_thread(thread_id):
        return None, (jsonify({"error": "no such thread"}), 404)
    return (clue_id, thread_id), None


@bp.route("/api/link", methods=["POST"])
def api_link():
    if (err := need_edit()):
        return err
    pair, err = _link_pair()
    if err:
        return err
    with _db.LOCK:
        # A repeat link is a no-op rather than an error: the frontend fires this
        # from a picker that does not know what is already linked, and "you
        # already did that" is not information the player needs.
        _conn().execute(
            "INSERT INTO clue_thread (clue_id, thread_id) VALUES (?, ?)"
            " ON CONFLICT DO NOTHING", pair)
        _conn().commit()
    return jsonify({"ok": True})


@bp.route("/api/link", methods=["DELETE"])
def api_unlink():
    if (err := need_edit()):
        return err
    pair, err = _link_pair()
    if err:
        return err
    with _db.LOCK:
        _conn().execute(
            "DELETE FROM clue_thread WHERE clue_id = ? AND thread_id = ?", pair)
        _conn().commit()
    return jsonify({"ok": True})


@bp.route("/api/threads/<int:thread_id>/solve", methods=["POST"])
def api_thread_solve(thread_id):
    """Close a thread, and by default spend the clues that fed it.

    The cascade is the reason this is its own route rather than a PATCH. Without
    it the ledger rots: you solve things, never go back to demote the clues, and
    the "understood but unused" list fills with clues you already spent until
    you stop trusting it. Clues already marked used are left alone, so the count
    returned is how many actually changed.
    """
    if (err := need_edit()):
        return err
    if not _one_thread(thread_id):
        return jsonify({"error": "no such thread"}), 404
    b = _body()
    mark = b.get("mark_clues_used", True)
    now = _now()
    with _db.LOCK:
        _conn().execute("""
            UPDATE thread SET state = 'solved', solution = ?, solved_at = ?,
                              updated_at = ?
            WHERE id = ?
        """, (b.get("solution"), now, now, thread_id))
        marked = 0
        if mark:
            cur = _conn().execute("""
                UPDATE clue SET state = 'used', updated_at = ?
                WHERE state != 'used' AND id IN (
                    SELECT clue_id FROM clue_thread WHERE thread_id = ?
                )
            """, (now, thread_id))
            marked = cur.rowcount
        _conn().commit()
    return jsonify({"thread": _one_thread(thread_id), "clues_marked": marked})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_lamulana_api.py -v`
Expected: PASS, 24 passed

- [ ] **Step 5: Commit**

```bash
git add lamulana/api.py tests/test_lamulana_api.py
git commit -m "feat(lamulana): link clues to threads, and spend them on solve"
```

---

## Task 7: Search and checklist

**Files:**
- Modify: `lamulana/api.py`
- Test: `tests/test_lamulana_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_lamulana_api.py`:

```python
def test_search_spans_clues_and_threads(editor_client):
    editor_client.post("/lamulana/api/clues", json={
        "title": "serpent tablet", "body": "where the twin serpents meet"})
    editor_client.post("/lamulana/api/threads", json={
        "title": "serpent door", "body": "won't open"})
    data = editor_client.get("/lamulana/api/search?q=serpent").get_json()
    assert len(data["clues"]) == 1
    assert len(data["threads"]) == 1


def test_search_ands_every_word(editor_client):
    editor_client.post("/lamulana/api/clues", json={
        "title": "a", "body": "where the twin serpents meet"})
    editor_client.post("/lamulana/api/clues", json={"title": "b", "body": "twin peaks"})
    hits = editor_client.get("/lamulana/api/search?q=twin serpents").get_json()["clues"]
    assert [c["title"] for c in hits] == ["a"]


def test_search_matches_interpretation_too(editor_client):
    editor_client.post("/lamulana/api/clues", json={
        "title": "a", "interpretation": "this is about the Valhalla ankh"})
    hits = editor_client.get("/lamulana/api/search?q=valhalla").get_json()["clues"]
    assert len(hits) == 1


def test_empty_search_returns_nothing(editor_client):
    editor_client.post("/lamulana/api/clues", json={"title": "a"})
    data = editor_client.get("/lamulana/api/search?q=").get_json()
    assert data == {"clues": [], "threads": []}


def test_checklist_toggle_stamps_and_clears_done_at(editor_client):
    groups = editor_client.get("/lamulana/api/checklist").get_json()["groups"]
    item = groups[0]["items"][0]
    r = editor_client.patch(f"/lamulana/api/checklist/{item['id']}", json={"done": True})
    assert r.get_json()["item"]["done"] is True
    assert r.get_json()["item"]["done_at"] > 0
    r = editor_client.patch(f"/lamulana/api/checklist/{item['id']}", json={"done": False})
    assert r.get_json()["item"]["done"] is False
    assert r.get_json()["item"]["done_at"] is None


def test_checklist_note(editor_client):
    item = editor_client.get("/lamulana/api/checklist").get_json()["groups"][0]["items"][0]
    r = editor_client.patch(f"/lamulana/api/checklist/{item['id']}",
                             json={"note": "behind the ice"})
    assert r.get_json()["item"]["note"] == "behind the ice"


def test_add_and_remove_a_custom_row(editor_client):
    r = editor_client.post("/lamulana/api/checklist",
                            json={"group": "Guardians", "name": "my own note"})
    assert r.status_code == 200
    new_id = r.get_json()["item"]["id"]
    groups = {g["group"]: g for g in
              editor_client.get("/lamulana/api/checklist").get_json()["groups"]}
    assert len(groups["Guardians"]["items"]) == 11
    assert editor_client.delete(f"/lamulana/api/checklist/{new_id}").status_code == 200
    groups = {g["group"]: g for g in
              editor_client.get("/lamulana/api/checklist").get_json()["groups"]}
    assert len(groups["Guardians"]["items"]) == 10


def test_a_custom_row_can_open_a_new_group(editor_client):
    editor_client.post("/lamulana/api/checklist",
                        json={"group": "Garbs", "name": "Clay Doll Suit"})
    groups = {g["group"]: g for g in
              editor_client.get("/lamulana/api/checklist").get_json()["groups"]}
    assert [i["name"] for i in groups["Garbs"]["items"]] == ["Clay Doll Suit"]


def test_duplicate_custom_row_is_rejected(editor_client):
    editor_client.post("/lamulana/api/checklist", json={"group": "Garbs", "name": "x"})
    r = editor_client.post("/lamulana/api/checklist", json={"group": "Garbs", "name": "X"})
    assert r.status_code == 409


def test_checklist_writes_need_an_editing_session(reader_client):
    assert reader_client.patch("/lamulana/api/checklist/1", json={}).status_code == 403
    assert reader_client.post("/lamulana/api/checklist", json={}).status_code == 403
    assert reader_client.delete("/lamulana/api/checklist/1").status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_lamulana_api.py -v`
Expected: FAIL — 404 on `/lamulana/api/search`

- [ ] **Step 3: Implement search and the checklist**

Append to `lamulana/api.py`:

```python
# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@bp.route("/api/search")
def api_search():
    """One query across both kinds. An empty query matches nothing, not everything."""
    q = request.args.get("q", "")
    cc, cp = _search_terms(q, ["c.title", "c.body", "c.interpretation", "c.room"])
    tc, tp = _search_terms(q, ["t.title", "t.body", "t.solution"])
    if not cc:
        return jsonify({"clues": [], "threads": []})
    clues = _conn().execute(
        CLUE_SELECT + " WHERE " + cc + " ORDER BY c.updated_at DESC", cp).fetchall()
    threads = _conn().execute(
        THREAD_SELECT + " WHERE " + tc
        + " ORDER BY t.state = 'solved', t.updated_at DESC", tp).fetchall()
    return jsonify({"clues": _clue_json(clues),
                    "threads": [dict(r) for r in threads]})


# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------

def _one_item(item_id):
    row = _conn().execute(
        "SELECT id, group_name, name, position, done, done_at, note"
        " FROM checklist_item WHERE id = ?", (item_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["group"] = item.pop("group_name")
    item["done"] = bool(item["done"])
    return item


@bp.route("/api/checklist")
def api_checklist():
    return jsonify({"groups": _checklist_groups()})


@bp.route("/api/checklist/<int:item_id>", methods=["PATCH"])
def api_checklist_patch(item_id):
    if (err := need_edit()):
        return err
    if not _one_item(item_id):
        return jsonify({"error": "no such item"}), 404
    b = _body()
    with _db.LOCK:
        if "done" in b:
            done = bool(b["done"])
            # done_at is cleared on untick rather than left behind, so it always
            # means "when this was ticked", never "when it was ticked once".
            _conn().execute(
                "UPDATE checklist_item SET done = ?, done_at = ? WHERE id = ?",
                (1 if done else 0, _now() if done else None, item_id))
        if "note" in b:
            _conn().execute("UPDATE checklist_item SET note = ? WHERE id = ?",
                            (b["note"], item_id))
        _conn().commit()
    return jsonify({"item": _one_item(item_id)})


@bp.route("/api/checklist", methods=["POST"])
def api_checklist_add():
    if (err := need_edit()):
        return err
    b = _body()
    group = (b.get("group") or "").strip()
    name = (b.get("name") or "").strip()
    if not group or not name:
        return jsonify({"error": "group and name required"}), 400
    row = _conn().execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM checklist_item"
        " WHERE group_name = ?", (group,)).fetchone()
    try:
        with _db.LOCK:
            cur = _conn().execute(
                "INSERT INTO checklist_item (group_name, name, position)"
                " VALUES (?, ?, ?)", (group, name, row["p"]))
            _conn().commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "already on the list"}), 409
    return jsonify({"item": _one_item(cur.lastrowid)})


@bp.route("/api/checklist/<int:item_id>", methods=["DELETE"])
def api_checklist_delete(item_id):
    if (err := need_edit()):
        return err
    with _db.LOCK:
        _conn().execute("DELETE FROM checklist_item WHERE id = ?", (item_id,))
        _conn().commit()
    return jsonify({"ok": True})
```

The `sqlite3.IntegrityError` above needs the import. At the top of
`lamulana/api.py`, change `import os` / `import time` to include it:

```python
import os
import sqlite3
import time
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_lamulana_api.py -v`
Expected: PASS, 34 passed

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: everything passes

- [ ] **Step 6: Commit**

```bash
git add lamulana/api.py tests/test_lamulana_api.py
git commit -m "feat(lamulana): search across both kinds, checklist read and write"
```

---

## Task 8: Frontend shell — layout, tabs, filter rail

**Files:**
- Rewrite: `templates/lamulana.html`

No test: this is layout. Tasks 8–11 build the page up; verify by eye at the end
of each with the dev server running (`.venv/bin/python server.py`, then
`http://localhost:5050/lamulana/`).

- [ ] **Step 1: Write the page shell**

Replace `templates/lamulana.html` entirely:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#12100c">
<title>Eg-Lana</title>
<style>
  /* Same bones as recipes.html so this reads as the same site, with a warm
     ochre accent instead of blue -- the two pages are never confused at a
     glance, and this one is stone and torchlight rather than a kitchen. */
  :root{
    --bg:#12100c; --panel:#1a1712; --panel2:#221e17; --line:rgba(255,255,255,.08);
    --txt:#ece7dd; --dim:rgba(236,231,221,.45); --accent:#d9a441; --go:#3d8b5f;
    --raw:#8a8378; --understood:#d9a441; --used:#4a4640;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);
    font:400 15px/1.45 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;
    -webkit-font-smoothing:antialiased}
  body{display:flex;flex-direction:column;overflow:hidden}

  header{flex:none;display:flex;align-items:center;gap:14px;padding:9px 16px;
    background:var(--panel);border-bottom:1px solid var(--line)}
  header h1{font-size:16px;font-weight:600;letter-spacing:.02em;margin:0;flex:none}
  header h1 span{color:var(--accent)}
  .tabs{display:flex;gap:2px;flex:none}
  .tabs button{background:none;border:0;color:var(--dim);font:inherit;padding:5px 12px;
    border-radius:7px;cursor:pointer}
  .tabs button.on{background:var(--panel2);color:var(--txt)}
  .search{flex:1;max-width:420px;margin-left:auto;padding:7px 11px;border-radius:8px;
    background:var(--panel2);border:1px solid var(--line);color:var(--txt);
    font:inherit}
  .search:focus{outline:none;border-color:var(--accent)}
  .who{flex:none;color:var(--dim);font-size:13px}

  main{flex:1;display:flex;min-height:0}
  .rail{flex:none;width:190px;overflow-y:auto;padding:10px 0 30px;
    border-right:1px solid var(--line);background:var(--panel)}
  .rail h3{font-size:11px;letter-spacing:.11em;text-transform:uppercase;
    color:var(--dim);margin:14px 14px 6px;font-weight:600}
  .rail h3:first-child{margin-top:2px}
  .rail button{display:flex;width:100%;gap:8px;align-items:baseline;background:none;
    border:0;color:var(--dim);font:inherit;text-align:left;padding:4px 14px;
    cursor:pointer;border-left:2px solid transparent}
  .rail button:hover{color:var(--txt)}
  .rail button.on{color:var(--txt);border-left-color:var(--accent);
    background:rgba(217,164,65,.07)}
  .rail button b{font-weight:400;flex:1;overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap}
  .rail button i{font-style:normal;font-size:12px;color:var(--dim);flex:none}

  .list{flex:none;width:330px;overflow-y:auto;border-right:1px solid var(--line)}
  .list .row{padding:9px 14px;border-bottom:1px solid rgba(255,255,255,.04);
    cursor:pointer}
  .list .row:hover{background:var(--panel)}
  .list .row.on{background:var(--panel2)}
  .list .row b{display:block;font-weight:500;font-size:14px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .list .row small{display:block;color:var(--dim);font-size:12px;margin-top:2px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .list .empty{padding:26px 16px;color:var(--dim);font-size:13px}

  .pill{display:inline-block;font-size:10px;letter-spacing:.07em;text-transform:uppercase;
    padding:1px 6px;border-radius:20px;vertical-align:1px;margin-right:6px}
  .pill.raw{background:var(--raw);color:#12100c}
  .pill.understood{background:var(--understood);color:#12100c}
  .pill.used{background:var(--used);color:var(--dim)}
  .pill.open{background:var(--understood);color:#12100c}
  .pill.solved{background:var(--used);color:var(--dim)}

  .detail{flex:1;overflow-y:auto;padding:20px 26px 60px;min-width:0}
  .detail .empty{color:var(--dim);margin-top:40px}
  .detail h2{font-size:20px;margin:0 0 4px;font-weight:600}
  .detail .where{color:var(--dim);font-size:13px;margin-bottom:16px}
  .detail .quote{background:var(--panel);border-left:2px solid var(--accent);
    padding:11px 14px;margin:0 0 16px;white-space:pre-wrap;font-size:15px;
    line-height:1.55}
  .detail label{display:block;font-size:11px;letter-spacing:.11em;
    text-transform:uppercase;color:var(--dim);margin:16px 0 5px;font-weight:600}
  .fld{display:block;width:100%;padding:9px 11px;border-radius:8px;
    background:var(--panel2);border:1px solid var(--line);color:var(--txt);
    font:inherit}
  .fld:focus{outline:none;border-color:var(--accent)}
  textarea.fld{line-height:1.5;resize:vertical;min-height:70px}
  .btn{background:var(--panel2);border:1px solid var(--line);color:var(--txt);
    font:inherit;padding:7px 13px;border-radius:8px;cursor:pointer}
  .btn:hover{border-color:var(--accent)}
  .btn.go{background:var(--accent);border-color:var(--accent);color:#12100c;
    font-weight:500}
  .btn.danger:hover{border-color:#b4483c;color:#e0796d}
  .btns{display:flex;gap:8px;margin-top:18px;flex-wrap:wrap}

  .modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;
    align-items:flex-start;justify-content:center;padding-top:9vh;z-index:20}
  .modal .box{background:var(--panel);border:1px solid var(--line);border-radius:12px;
    width:min(560px,92vw);padding:18px 20px 20px;max-height:80vh;overflow-y:auto}
  .modal h3{margin:0 0 14px;font-size:16px;font-weight:600}
  .modal .two{display:flex;gap:10px}
  .modal .two > *{flex:1;min-width:0}

  .keys{position:fixed;bottom:0;left:0;right:0;padding:5px 16px;font-size:11px;
    color:var(--dim);background:var(--panel);border-top:1px solid var(--line)}
  .keys b{color:var(--txt);font-weight:500}
</style>
</head>
<body>
<header>
  <h1>Eg-<span>Lana</span></h1>
  <div class="tabs">
    <button data-tab="clues" class="on">Clues</button>
    <button data-tab="threads">Threads</button>
    <button data-tab="progress">Progress</button>
  </div>
  <input class="search" placeholder="Search everything…  /">
  <div class="who"></div>
</header>

<main>
  <div class="rail"></div>
  <div class="list"></div>
  <div class="detail"><div class="empty">Nothing selected.</div></div>
</main>

<div class="keys">
  <b>n</b> new clue · <b>N</b> new thread · <b>/</b> search ·
  <b>j/k</b> move · <b>l</b> link · <b>1–3</b> tabs · <b>esc</b> close
</div>

<script>
// ---------------------------------------------------------------------------
// State and plumbing
// ---------------------------------------------------------------------------
const S = {
  tab: 'clues',
  areas: [], areaById: {},
  clues: [], threads: [], checklist: [],
  counts: {},
  filterArea: null, filterState: null,
  selected: null,          // id within the active tab
  detail: null,            // the loaded thread detail, when on the threads tab
  query: '',
  canEdit: false,
};

const $ = s => document.querySelector(s);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};

async function api(path, opts) {
  const r = await fetch('/lamulana/api/' + path, {
    headers: {'Content-Type': 'application/json'},
    ...opts,
  });
  if (r.status === 403) { alertOnce(); return null; }
  return r.json();
}

let warned = false;
function alertOnce() {
  if (warned) return;
  warned = true;
  $('.who').textContent = 'read-only — log in on the soundboard to edit';
}

async function boot() {
  const data = await api('bootstrap');
  S.areas = data.areas;
  S.areaById = Object.fromEntries(data.areas.map(a => [a.id, a.name]));
  S.checklist = data.checklist;
  S.counts = data.counts;
  const me = await (await fetch('/api/me')).json();
  S.canEdit = !!me.can_edit;
  if (!S.canEdit) alertOnce();
  await refresh();
}

async function refresh() {
  if (S.tab === 'clues') {
    const p = new URLSearchParams();
    if (S.filterArea) p.set('area', S.filterArea);
    if (S.filterState) p.set('state', S.filterState);
    if (S.query) p.set('q', S.query);
    S.clues = (await api('clues?' + p)).clues;
  } else if (S.tab === 'threads') {
    const p = new URLSearchParams();
    if (S.filterArea) p.set('area', S.filterArea);
    if (S.filterState) p.set('state', S.filterState);
    if (S.query) p.set('q', S.query);
    S.threads = (await api('threads?' + p)).threads;
  } else {
    S.checklist = (await api('checklist')).groups;
  }
  S.counts = (await api('bootstrap')).counts;
  render();
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
function render() {
  document.querySelectorAll('.tabs button').forEach(b =>
    b.classList.toggle('on', b.dataset.tab === S.tab));
  renderRail();
  renderList();
  renderDetail();
}

function renderRail() {
  const rail = $('.rail');
  rail.innerHTML = '';
  if (S.tab === 'progress') return;

  const states = S.tab === 'clues'
    ? [['raw', 'Raw'], ['understood', 'Understood'], ['used', 'Used']]
    : [['open', 'Open'], ['solved', 'Solved']];

  rail.appendChild(el('h3', null, 'State'));
  rail.appendChild(railButton('All', null, S.filterState === null,
    () => { S.filterState = null; S.selected = null; refresh(); }));
  states.forEach(([key, label]) => {
    rail.appendChild(railButton(label, null, S.filterState === key,
      () => { S.filterState = key; S.selected = null; refresh(); }));
  });

  rail.appendChild(el('h3', null, 'Area'));
  rail.appendChild(railButton('Everywhere', null, S.filterArea === null,
    () => { S.filterArea = null; S.selected = null; refresh(); }));
  S.areas.forEach(a => {
    rail.appendChild(railButton(a.name, null, S.filterArea === a.id,
      () => { S.filterArea = a.id; S.selected = null; refresh(); }));
  });
}

function railButton(label, count, on, onclick) {
  const b = el('button', on ? 'on' : '');
  b.appendChild(el('b', null, label));
  if (count != null) b.appendChild(el('i', null, String(count)));
  b.onclick = onclick;
  return b;
}

function renderList() {
  const list = $('.list');
  list.innerHTML = '';
  list.style.display = S.tab === 'progress' ? 'none' : '';
  if (S.tab === 'progress') return;

  const rows = S.tab === 'clues' ? S.clues : S.threads;
  if (!rows.length) {
    list.appendChild(el('div', 'empty',
      S.query ? 'Nothing matches.' : 'Nothing here yet.'));
    return;
  }
  rows.forEach(r => {
    const row = el('div', 'row' + (S.selected === r.id ? ' on' : ''));
    const title = el('b');
    title.appendChild(el('span', 'pill ' + r.state, r.state));
    title.appendChild(document.createTextNode(r.title));
    row.appendChild(title);
    const bits = [];
    if (r.area) bits.push(r.area + (r.room ? ' ' + r.room : ''));
    if (S.tab === 'clues' && r.threads.length) bits.push(r.threads.length + ' linked');
    if (S.tab === 'threads' && r.clue_count) bits.push(r.clue_count + ' clues');
    row.appendChild(el('small', null, bits.join(' · ')));
    row.onclick = () => select(r.id);
    list.appendChild(row);
  });
}

async function select(id) {
  S.selected = id;
  if (S.tab === 'threads') {
    S.detail = (await api('threads/' + id)).thread;
  }
  render();
}

function renderDetail() { /* filled in by Tasks 9 and 10 */ }

boot();
</script>
</body>
</html>
```

- [ ] **Step 2: Look at it**

Run: `.venv/bin/python server.py` and open `http://localhost:5050/lamulana/`
Expected: header with three tabs, empty filter rail with State and Area sections
listing all 28 areas, empty list column, "Nothing selected." on the right.

- [ ] **Step 3: Commit**

```bash
git add templates/lamulana.html
git commit -m "feat(lamulana): page shell, tabs, and the filter rail"
```

---

## Task 9: Clue detail and editing

**Files:**
- Modify: `templates/lamulana.html`

- [ ] **Step 1: Replace the `renderDetail` stub**

In `templates/lamulana.html`, replace the line
`function renderDetail() { /* filled in by Tasks 9 and 10 */ }` with:

```javascript
function renderDetail() {
  const d = $('.detail');
  d.innerHTML = '';
  if (S.tab === 'progress') { renderProgress(d); return; }
  if (!S.selected) {
    d.appendChild(el('div', 'empty', S.tab === 'clues'
      ? 'Select a clue, or press n to record one.'
      : 'Select a thread, or press N to open one.'));
    return;
  }
  if (S.tab === 'clues') renderClueDetail(d);
  else renderThreadDetail(d);
}

function renderClueDetail(d) {
  const c = S.clues.find(x => x.id === S.selected);
  if (!c) { d.appendChild(el('div', 'empty', 'Gone.')); return; }

  d.appendChild(el('h2', null, c.title));
  d.appendChild(el('div', 'where',
    [c.area, c.room, c.source].filter(Boolean).join(' · ')));
  if (c.body) d.appendChild(el('div', 'quote', c.body));

  d.appendChild(el('label', null, 'What I think it means'));
  const interp = el('textarea', 'fld');
  interp.value = c.interpretation || '';
  interp.placeholder = 'Nothing yet.';
  d.appendChild(interp);

  d.appendChild(el('label', null, 'State'));
  const state = el('select', 'fld');
  [['raw', 'Raw — copied it down, no idea'],
   ['understood', "Understood — know what it means, can't act yet"],
   ['used', 'Used — spent']].forEach(([v, label]) => {
    const o = el('option', null, label); o.value = v;
    if (c.state === v) o.selected = true;
    state.appendChild(o);
  });
  d.appendChild(state);

  if (c.threads.length) {
    d.appendChild(el('label', null, 'Feeds'));
    c.threads.forEach(t => {
      const row = el('div', 'row');
      row.style.cursor = 'pointer';
      row.appendChild(el('span', 'pill ' + t.state, t.state));
      row.appendChild(document.createTextNode(t.title));
      row.onclick = () => { S.tab = 'threads'; S.filterState = null; refresh()
        .then(() => select(t.id)); };
      d.appendChild(row);
    });
  }

  const btns = el('div', 'btns');
  const save = el('button', 'btn go', 'Save');
  save.onclick = async () => {
    await api('clues/' + c.id, {method: 'PATCH', body: JSON.stringify({
      interpretation: interp.value, state: state.value})});
    refresh();
  };
  const link = el('button', 'btn', 'Link to a thread');
  link.onclick = () => linkPicker(c.id);
  const del = el('button', 'btn danger', 'Delete');
  del.onclick = async () => {
    await api('clues/' + c.id, {method: 'DELETE'});
    S.selected = null; refresh();
  };
  btns.append(save, link, del);
  d.appendChild(btns);
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------
function modal(title, build) {
  const back = el('div', 'modal');
  const box = el('div', 'box');
  box.appendChild(el('h3', null, title));
  back.appendChild(box);
  back.onclick = e => { if (e.target === back) back.remove(); };
  document.body.appendChild(back);
  build(box, () => back.remove());
  const first = box.querySelector('input, textarea, select');
  if (first) first.focus();
  return back;
}

const LAST_AREA = 'lamulana_last_area';

function newClue() {
  if (!S.canEdit) return alertOnce();
  modal('Record a clue', (box, close) => {
    const two = el('div', 'two');
    const area = el('select', 'fld');
    area.appendChild(el('option', null, '— area —'));
    S.areas.forEach(a => {
      const o = el('option', null, a.name); o.value = a.id;
      if (String(a.id) === localStorage.getItem(LAST_AREA)) o.selected = true;
      area.appendChild(o);
    });
    const room = el('input', 'fld');
    room.placeholder = 'Room (E-3)';
    room.setAttribute('list', 'roomlist');
    two.append(area, room);
    box.appendChild(two);

    const title = el('input', 'fld');
    title.placeholder = 'Title — how you\'ll recognise it';
    title.style.marginTop = '10px';
    box.appendChild(title);

    const body = el('textarea', 'fld');
    body.placeholder = 'The text, as the game gave it';
    body.style.marginTop = '10px';
    body.style.minHeight = '130px';
    box.appendChild(body);

    const source = el('select', 'fld');
    [['tablet', 'Tablet'], ['npc', 'NPC'], ['mail', 'Mail'],
     ['other', 'Other']].forEach(([v, label]) => {
      const o = el('option', null, label); o.value = v; source.appendChild(o);
    });
    source.style.marginTop = '10px';
    box.appendChild(source);

    const btns = el('div', 'btns');
    const save = el('button', 'btn go', 'Save');
    save.onclick = async () => {
      if (!title.value.trim()) return title.focus();
      if (area.value) localStorage.setItem(LAST_AREA, area.value);
      await api('clues', {method: 'POST', body: JSON.stringify({
        title: title.value, body: body.value, room: room.value,
        source: source.value, area_id: area.value ? +area.value : null})});
      close(); S.tab = 'clues'; refresh();
    };
    const cancel = el('button', 'btn', 'Cancel');
    cancel.onclick = close;
    btns.append(save, cancel);
    box.appendChild(btns);
    title.focus();
  });
}

function newThread() {
  if (!S.canEdit) return alertOnce();
  modal('Open a thread', (box, close) => {
    const area = el('select', 'fld');
    area.appendChild(el('option', null, '— area —'));
    S.areas.forEach(a => {
      const o = el('option', null, a.name); o.value = a.id;
      if (String(a.id) === localStorage.getItem(LAST_AREA)) o.selected = true;
      area.appendChild(o);
    });
    box.appendChild(area);

    const title = el('input', 'fld');
    title.placeholder = "What's blocking you";
    title.style.marginTop = '10px';
    box.appendChild(title);

    const body = el('textarea', 'fld');
    body.placeholder = 'What you have tried, what you suspect you need';
    body.style.marginTop = '10px';
    box.appendChild(body);

    const btns = el('div', 'btns');
    const save = el('button', 'btn go', 'Open it');
    save.onclick = async () => {
      if (!title.value.trim()) return title.focus();
      await api('threads', {method: 'POST', body: JSON.stringify({
        title: title.value, body: body.value,
        area_id: area.value ? +area.value : null})});
      close(); S.tab = 'threads'; refresh();
    };
    const cancel = el('button', 'btn', 'Cancel');
    cancel.onclick = close;
    btns.append(save, cancel);
    box.appendChild(btns);
    title.focus();
  });
}

async function linkPicker(clueId) {
  if (!S.canEdit) return alertOnce();
  const all = (await api('threads')).threads;
  modal('Link to a thread', (box, close) => {
    if (!all.length) {
      box.appendChild(el('div', 'empty', 'No threads yet — press N to open one.'));
      return;
    }
    all.forEach(t => {
      const row = el('div', 'row');
      row.style.cursor = 'pointer';
      row.appendChild(el('span', 'pill ' + t.state, t.state));
      row.appendChild(document.createTextNode(t.title));
      row.onclick = async () => {
        await api('link', {method: 'POST',
          body: JSON.stringify({clue_id: clueId, thread_id: t.id})});
        close(); refresh();
      };
      box.appendChild(row);
    });
  });
}
```

- [ ] **Step 2: Add the room datalist**

Immediately before `</body>` in `templates/lamulana.html`, add:

```html
<datalist id="roomlist"></datalist>
```

and inside `boot()`, after `S.canEdit = !!me.can_edit;`, add:

```javascript
  const rooms = await api('rooms');
  $('#roomlist').innerHTML = '';
  rooms.rooms.forEach(r => {
    const o = document.createElement('option'); o.value = r;
    $('#roomlist').appendChild(o);
  });
```

- [ ] **Step 3: Look at it**

With the server running, click "Clues", press nothing yet — the detail pane
should read "Select a clue, or press n to record one." Keyboard comes in Task 11,
so for now call `newClue()` from the browser console to check the modal renders,
saves, and the new clue appears in the list and opens in the detail pane.

- [ ] **Step 4: Commit**

```bash
git add templates/lamulana.html
git commit -m "feat(lamulana): clue detail, capture modal, and the link picker"
```

---

## Task 10: Thread detail and the progress tab

**Files:**
- Modify: `templates/lamulana.html`

- [ ] **Step 1: Add the thread detail and progress renderers**

Append to the `<script>` block in `templates/lamulana.html`, before `boot();`:

```javascript
function renderThreadDetail(d) {
  const t = S.detail;
  if (!t || t.id !== S.selected) { d.appendChild(el('div', 'empty', 'Loading…')); return; }

  d.appendChild(el('h2', null, t.title));
  const where = el('div', 'where');
  where.appendChild(el('span', 'pill ' + t.state, t.state));
  where.appendChild(document.createTextNode(t.area || 'no area'));
  d.appendChild(where);

  d.appendChild(el('label', null, 'Notes'));
  const body = el('textarea', 'fld');
  body.value = t.body || '';
  body.placeholder = 'What you have tried, what you suspect you need';
  d.appendChild(body);

  if (t.state === 'solved' && t.solution) {
    d.appendChild(el('label', null, 'Solution'));
    d.appendChild(el('div', 'quote', t.solution));
  }

  // The reason this page exists: every linked clue at full length, so the
  // scattered text that feeds one puzzle is on one screen.
  d.appendChild(el('label', null, t.clues.length
    ? 'Clues feeding this (' + t.clues.length + ')'
    : 'No clues linked yet'));
  t.clues.forEach(c => {
    const wrap = el('div');
    wrap.style.marginBottom = '14px';
    const head = el('div');
    head.appendChild(el('span', 'pill ' + c.state, c.state));
    const name = el('b', null, c.title);
    name.style.cursor = 'pointer';
    name.onclick = () => { S.tab = 'clues'; S.filterState = null; S.filterArea = null;
      refresh().then(() => select(c.id)); };
    head.appendChild(name);
    const from = el('small', null, '  ' + [c.area, c.room].filter(Boolean).join(' '));
    from.style.color = 'var(--dim)';
    head.appendChild(from);
    wrap.appendChild(head);
    if (c.body) wrap.appendChild(el('div', 'quote', c.body));
    if (c.interpretation) {
      const i = el('div', null, c.interpretation);
      i.style.color = 'var(--dim)';
      i.style.fontSize = '13px';
      wrap.appendChild(i);
    }
    const unlink = el('button', 'btn', 'Unlink');
    unlink.style.marginTop = '6px';
    unlink.onclick = async () => {
      await api('link', {method: 'DELETE',
        body: JSON.stringify({clue_id: c.id, thread_id: t.id})});
      select(t.id);
    };
    wrap.appendChild(unlink);
    d.appendChild(wrap);
  });

  const btns = el('div', 'btns');
  const save = el('button', 'btn go', 'Save notes');
  save.onclick = async () => {
    await api('threads/' + t.id, {method: 'PATCH',
      body: JSON.stringify({body: body.value})});
    select(t.id); refresh();
  };
  btns.appendChild(save);

  if (t.state === 'open') {
    const solve = el('button', 'btn', 'Solve it');
    solve.onclick = () => solveDialog(t);
    btns.appendChild(solve);
  } else {
    const reopen = el('button', 'btn', 'Reopen');
    reopen.onclick = async () => {
      await api('threads/' + t.id, {method: 'PATCH',
        body: JSON.stringify({state: 'open'})});
      select(t.id); refresh();
    };
    btns.appendChild(reopen);
  }

  const del = el('button', 'btn danger', 'Delete');
  del.onclick = async () => {
    await api('threads/' + t.id, {method: 'DELETE'});
    S.selected = null; S.detail = null; refresh();
  };
  btns.appendChild(del);
  d.appendChild(btns);
}

function solveDialog(t) {
  modal('Solve: ' + t.title, (box, close) => {
    const sol = el('textarea', 'fld');
    sol.placeholder = 'What the answer turned out to be';
    box.appendChild(sol);

    const wrap = el('label');
    wrap.style.textTransform = 'none';
    wrap.style.letterSpacing = '0';
    wrap.style.fontSize = '13px';
    wrap.style.color = 'var(--txt)';
    const mark = el('input');
    mark.type = 'checkbox';
    mark.checked = true;
    wrap.appendChild(mark);
    wrap.appendChild(document.createTextNode(
      ' Mark the ' + t.clues.length + ' linked clue(s) as used'));
    box.appendChild(wrap);

    const btns = el('div', 'btns');
    const go = el('button', 'btn go', 'Solved');
    go.onclick = async () => {
      await api('threads/' + t.id + '/solve', {method: 'POST',
        body: JSON.stringify({solution: sol.value, mark_clues_used: mark.checked})});
      close(); await select(t.id); refresh();
    };
    const cancel = el('button', 'btn', 'Cancel');
    cancel.onclick = close;
    btns.append(go, cancel);
    box.appendChild(btns);
  });
}

function renderProgress(d) {
  S.checklist.forEach(group => {
    const done = group.items.filter(i => i.done).length;
    const h = el('label', null, group.group + ' — ' + done + '/' + group.items.length);
    d.appendChild(h);
    group.items.forEach(item => {
      const row = el('div', 'row');
      row.style.display = 'flex';
      row.style.alignItems = 'baseline';
      row.style.gap = '10px';
      const box = el('input');
      box.type = 'checkbox';
      box.checked = item.done;
      box.onchange = async () => {
        await api('checklist/' + item.id, {method: 'PATCH',
          body: JSON.stringify({done: box.checked})});
        refresh();
      };
      const name = el('span', null, item.name);
      if (item.done) name.style.color = 'var(--dim)';
      const note = el('input', 'fld');
      note.value = item.note || '';
      note.placeholder = 'note';
      note.style.flex = '0 0 200px';
      note.style.marginLeft = 'auto';
      note.onchange = async () => {
        await api('checklist/' + item.id, {method: 'PATCH',
          body: JSON.stringify({note: note.value})});
      };
      row.append(box, name, note);
      d.appendChild(row);
    });

    const add = el('button', 'btn', '+ add a row');
    add.style.margin = '8px 0 4px';
    add.onclick = () => {
      const name = prompt('Add to ' + group.group + ':');
      if (name) api('checklist', {method: 'POST',
        body: JSON.stringify({group: group.group, name})}).then(refresh);
    };
    d.appendChild(add);
  });
}
```

Note: `prompt()` is a browser dialog and is fine here — it is user-triggered from
a click, unlike the automation constraints that apply to scripted testing.

- [ ] **Step 2: Look at it**

With the server running: open a thread, link a clue to it from the Clues tab,
then reopen the thread — the clue's full text should appear inline. Solve it with
the checkbox ticked and confirm the clue's pill flips to `used`. Check the
Progress tab lists all five groups with the right counts (10, 10, 10, 16, 24).

- [ ] **Step 3: Commit**

```bash
git add templates/lamulana.html
git commit -m "feat(lamulana): thread detail with inline clues, solve dialog, progress tab"
```

---

## Task 11: Keyboard and search wiring

**Files:**
- Modify: `templates/lamulana.html`

- [ ] **Step 1: Wire the header and the keyboard**

Append to the `<script>` block, before `boot();`:

```javascript
// ---------------------------------------------------------------------------
// Header and keyboard
// ---------------------------------------------------------------------------
document.querySelectorAll('.tabs button').forEach(b => {
  b.onclick = () => setTab(b.dataset.tab);
});

function setTab(tab) {
  S.tab = tab;
  S.selected = null;
  S.detail = null;
  S.filterState = null;
  refresh();
}

let searchTimer = null;
$('.search').addEventListener('input', e => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    S.query = e.target.value.trim();
    S.selected = null;
    refresh();
  }, 180);
});

function typing() {
  const t = document.activeElement && document.activeElement.tagName;
  return t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT';
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    const m = document.querySelector('.modal');
    if (m) { m.remove(); return; }
    if (document.activeElement === $('.search')) {
      $('.search').value = ''; S.query = ''; $('.search').blur(); refresh();
    }
    return;
  }
  if (typing() || e.metaKey || e.ctrlKey || e.altKey) return;
  if (document.querySelector('.modal')) return;

  const rows = S.tab === 'clues' ? S.clues : S.threads;
  const at = rows.findIndex(r => r.id === S.selected);

  switch (e.key) {
    case '/': e.preventDefault(); $('.search').focus(); break;
    case 'n': e.preventDefault(); newClue(); break;
    case 'N': e.preventDefault(); newThread(); break;
    case '1': setTab('clues'); break;
    case '2': setTab('threads'); break;
    case '3': setTab('progress'); break;
    case 'j': if (rows.length) select(rows[Math.min(at + 1, rows.length - 1)].id); break;
    case 'k': if (rows.length) select(rows[Math.max(at - 1, 0)].id); break;
    case 'l':
      if (S.tab === 'clues' && S.selected) { e.preventDefault(); linkPicker(S.selected); }
      break;
  }
});
```

Note `j`/`k` with nothing selected: `findIndex` returns -1, so `j` selects index
0 and `k` clamps to index 0. Both land on the first row, which is what you want
from an empty selection.

- [ ] **Step 2: Verify every key**

With the server running, check each: `n` opens the clue modal, `N` the thread
modal, `/` focuses search, `Esc` closes a modal and clears a focused search,
`j`/`k` walk the list, `1`/`2`/`3` switch tabs, `l` opens the link picker on a
selected clue. Confirm typing "n" inside a textarea does not open a modal.

- [ ] **Step 3: Commit**

```bash
git add templates/lamulana.html
git commit -m "feat(lamulana): keyboard shortcuts and live search"
```

---

## Task 12: Full-suite check and deploy notes

**Files:**
- Create: `docs/LAMULANA.md`

- [ ] **Step 1: Run everything**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass, including the pre-existing soundboard and recipes suites

- [ ] **Step 2: Write the deploy note**

Create `docs/LAMULANA.md`:

```markdown
# La-Mulana 2 tracker

Lives at `/lamulana`. Blueprint in `lamulana/`, database at `data/lamulana.db`,
frontend in `templates/lamulana.html`. Reads are open; writes need the same
login the soundboard and recipes use.

## Deploying it

Production `server.py` has previously held code that existed in no commit, so it
is edited in place, never overwritten:

1. Copy `lamulana/` and `templates/lamulana.html` to the box.
2. Edit the production `server.py` by hand, adding the two lines after the
   existing `app.register_blueprint(recipes_bp)`:

       from lamulana import lamulana_bp
       app.register_blueprint(lamulana_bp)

3. Restart per `deploy/DEPLOY.md` (as root@, not sudo).

The database creates and seeds itself on first import. Nothing to run by hand.

## Adding to the seed

`lamulana/seed.py` holds the areas and checklist rows. Re-running the seed after
editing it is safe: it updates positions and inserts new rows, and never touches
a `done` flag or a note you wrote.
```

- [ ] **Step 3: Commit**

```bash
git add docs/LAMULANA.md
git commit -m "docs(lamulana): what it is and how to deploy it"
```

---

## Self-review notes

Checked against the spec:

- Placement, auth, schema, the three clue states, the solve cascade, no tags, no
  FTS5, migrations — Tasks 2, 4, 5, 6.
- Every route in the spec's table has a task: bootstrap (3), clues + rooms (4),
  threads (5), link + solve (6), search + checklist (7).
- Three tabs, filter rail, list, detail pane, threads-inline-clues, quick
  capture with remembered area, keyboard map — Tasks 8–11.
- Seed rules (two-source where ambiguous, leave out rather than guess, sources
  named and dated, idempotent, custom rows allowed) — Task 1 and `seed_checklist`.
- Test list — Tasks 2 and 4–7 cover every bullet in the spec's testing section.
- Deploy note — Task 12.

One deliberate deviation, also amended in the spec: `lamulana/db.py` uses a plain
`MIGRATIONS` list rather than copying the column-reflection engine from
`recipes/db.py`, because no `lamulana.db` exists in the world yet.
