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
--
-- These CHECK lists must match CLUE_STATES / CLUE_SOURCES in lamulana/api.py.
-- The route layer validates first and means to reject every bad value before
-- it reaches here, so the constraints below are meant to never fire -- but
-- edit both sides together, since adding a value to only one means either a
-- legal value 400s at the route or an illegal one 500s on this CHECK.
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
