"""Schema, seed data, and store-order query for the recipes + grocery list feature.

Exposes `connect()`, `init_schema()`, and `seed_sections()` as plain functions
rather than a module-level connection: callers (tests today, a later
runtime-owning module going forward) pass in the path and drive the lifecycle
themselves. Long-lived server use goes through `get_conn()`, which hands each
thread its own connection — see that function for why sharing one is not an
option.

Schema changes go through `migrate()`, which every caller gets for free via
`init_schema()`. `CREATE TABLE IF NOT EXISTS` cannot add a column to a table
that already exists, so a database created before a column was added stays
broken until something ALTERs it — see `_sync_columns` for how that is now
handled automatically, and `MIGRATIONS` for the changes it cannot express.
"""

import sqlite3
import threading

from .seed import SECTIONS

LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS section (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subsection (
    id         INTEGER PRIMARY KEY,
    section_id INTEGER NOT NULL REFERENCES section(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    position   INTEGER NOT NULL,
    UNIQUE(section_id, name COLLATE NOCASE)
);

-- COLLATE NOCASE on name/alias: every planned lookup and merge is
-- case-insensitive ("Onions" and "onions" are the same grocery thing), so
-- uniqueness has to agree with that or two rows can exist for one product.
CREATE TABLE IF NOT EXISTS pantry_item (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL COLLATE NOCASE UNIQUE,
    subsection_id  INTEGER REFERENCES subsection(id) ON DELETE SET NULL,
    is_staple      INTEGER NOT NULL DEFAULT 0,
    buy_unit       TEXT,
    shaws_url      TEXT,
    shaws_sku      TEXT,
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS pantry_alias (
    pantry_item_id INTEGER NOT NULL REFERENCES pantry_item(id) ON DELETE CASCADE,
    alias          TEXT NOT NULL COLLATE NOCASE UNIQUE
);

CREATE TABLE IF NOT EXISTS recipe (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    source_name  TEXT,
    source_url   TEXT,
    servings     INTEGER,
    time_minutes INTEGER,
    instructions TEXT,
    notes        TEXT,
    photo_url    TEXT,
    created_by   TEXT,
    created_at   INTEGER NOT NULL,
    archived     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recipe_ingredient (
    id             INTEGER PRIMARY KEY,
    recipe_id      INTEGER NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    raw_text       TEXT NOT NULL,
    qty            REAL,
    unit           TEXT,
    -- The prep clause ("diced") is parsed once, when the recipe is saved, and
    -- stored here rather than re-derived on every read. Re-parsing would mean
    -- a later tweak to parse.py silently rewrites what already-saved recipes
    -- appear to say -- history changing under a recipe nobody edited.
    prep           TEXT,
    pantry_item_id INTEGER REFERENCES pantry_item(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS list_line (
    id             INTEGER PRIMARY KEY,
    pantry_item_id INTEGER REFERENCES pantry_item(id) ON DELETE SET NULL,
    free_text      TEXT,
    checked        INTEGER NOT NULL DEFAULT 0,
    checked_by     TEXT,
    checked_at     INTEGER,
    created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS list_contribution (
    id           INTEGER PRIMARY KEY,
    list_line_id INTEGER NOT NULL REFERENCES list_line(id) ON DELETE CASCADE,
    recipe_id    INTEGER REFERENCES recipe(id) ON DELETE CASCADE,
    added_by     TEXT,
    qty          REAL,
    unit         TEXT,
    raw_text     TEXT
);

CREATE TABLE IF NOT EXISTS meal_plan (
    recipe_id INTEGER PRIMARY KEY REFERENCES recipe(id) ON DELETE CASCADE,
    added_by  TEXT,
    added_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ing_recipe   ON recipe_ingredient(recipe_id);
CREATE INDEX IF NOT EXISTS idx_ing_pantry   ON recipe_ingredient(pantry_item_id);
CREATE INDEX IF NOT EXISTS idx_contrib_line ON list_contribution(list_line_id);
CREATE INDEX IF NOT EXISTS idx_contrib_rcp  ON list_contribution(recipe_id);
CREATE INDEX IF NOT EXISTS idx_line_pantry  ON list_line(pantry_item_id);
CREATE INDEX IF NOT EXISTS idx_alias_pantry ON pantry_alias(pantry_item_id);

-- "One open line per grocery thing": two recipes wanting onions merge onto
-- the same unchecked line (their claims are separate list_contribution rows);
-- once a line is checked off, a fresh one is allowed to start. Partial
-- indexes only cover checked=0 rows so history isn't constrained.
CREATE UNIQUE INDEX IF NOT EXISTS idx_line_open_item
  ON list_line(pantry_item_id) WHERE checked = 0 AND pantry_item_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_line_open_text
  ON list_line(free_text) WHERE checked = 0 AND free_text IS NOT NULL;

-- A list_line with zero remaining list_contribution rows is nothing anyone
-- still wants — drop it rather than leaving an orphaned line on the shopping
-- list forever. Fires whether a contribution is deleted directly or cascaded
-- in via a deleted recipe.
CREATE TRIGGER IF NOT EXISTS trg_drop_childless_line
AFTER DELETE ON list_contribution
BEGIN
  DELETE FROM list_line WHERE id = OLD.list_line_id
    AND NOT EXISTS (SELECT 1 FROM list_contribution WHERE list_line_id = OLD.list_line_id);
END;
"""


def connect(path):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Per-thread connection cache. Lives on the module object deliberately: the
# test suite reloads this package under a fresh DATA_DIR, and a cache anchored
# anywhere longer-lived (a global registry, a class attribute on something
# imported elsewhere) would survive that reload and hand a test a connection to
# the previous test's database file.
_LOCAL = threading.local()


def get_conn(path):
    """The calling thread's connection to `path`, opened on first use.

    One `sqlite3.Connection` shared across waitress's 16 request threads was a
    real, measured bug, not a theoretical one. A connection carries a cache of
    prepared statements keyed by SQL text, so two threads running the *same*
    query — which is exactly what a four-second poll from every phone in the
    house produces — are handed the *same* statement object. Whichever one
    resets it first leaves the other fetching rows whose tuple is shorter than
    the column description they were built against, and that surfaces as
    `IndexError: tuple index out of range` from `dict(row)`: a 500 to somebody
    standing in an aisle.

    A connection each is what WAL mode is for. Readers do not block the writer
    and the writer does not block readers, so N shoppers polling while one adds
    an item all proceed. Writers are still serialised by `LOCK` above, which is
    what keeps the check-then-act in `_find_or_make_line` honest; at household
    scale that costs nothing.

    Connections are never closed. Waitress runs a fixed thread pool, so the
    count is bounded by that pool and by the number of distinct paths a process
    touches — one, outside tests.
    """
    cache = getattr(_LOCAL, "conns", None)
    if cache is None:
        cache = _LOCAL.conns = {}
    conn = cache.get(path)
    if conn is None:
        # Keyed by path as well, so even a cache that somehow outlived a reload
        # cannot answer with a connection to a different database file.
        conn = cache[path] = connect(path)
    return conn


class SchemaDriftError(RuntimeError):
    """SCHEMA declares something an existing database lacks and we can't add it.

    Always a programming error, never bad user data: someone changed SCHEMA in a
    way `ALTER TABLE ... ADD COLUMN` cannot express and didn't write the
    matching `MIGRATIONS` step. Raised at startup so it's caught on deploy
    rather than by whichever request first touches the column.
    """


# Ordered, run-once migration steps, for the changes `_sync_columns` below
# cannot make on its own: a NOT NULL column with no default, a changed
# collation, a data backfill, a dropped column.
#
# Append only, never reorder or delete — position in this list *is* the version
# number recorded in `meta.schema_version`.
#
# Each step MUST be idempotent anyway. Databases that predate this mechanism
# report version 0 whatever their actual shape, so an already-current database
# will run every step once. Guard on what the database actually looks like
# (`PRAGMA table_info`, `sqlite_master`), not on the version number.
#
# Empty today on purpose: the only column ever added to SCHEMA since the first
# release is `recipe_ingredient.prep`, which is nullable TEXT and therefore
# handled by `_sync_columns`. That is the intended outcome — reach for this list
# only when the automatic path provably cannot do the job.
#
# One older change deliberately has no step here. The first schema declared
# `pantry_item.name`, `pantry_alias.alias` and `subsection`'s UNIQUE without
# COLLATE NOCASE; a database in that shape would accept both "Onions" and
# "onions" as separate items, and only a full table rebuild can fix it. No such
# database can exist: nothing creates recipes.db except `recipes/api.py`, which
# was written after the collations landed. Adding a rebuild — DROP TABLE with
# foreign keys disabled, on tables holding the family's data — to guard an
# unreachable state is the riskier choice. If one ever turns up, this is where
# the step goes; `test_the_collation_drift_is_a_known_unfixed_limit` pins the
# current behaviour so the gap stays visible.
MIGRATIONS = []          # list of (name, callable taking a connection)

SCHEMA_VERSION = len(MIGRATIONS)


def _declared_tables():
    """What SCHEMA says each table should look like: {table: [table_info rows]}.

    Built by letting SQLite parse SCHEMA into a throwaway in-memory database and
    reading the shape back out, rather than regexing the DDL ourselves. SQLite
    is the only thing that reliably gets its own grammar right, and this stays
    correct for free as SCHEMA changes.
    """
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(SCHEMA)
        tables = [r[0] for r in ref.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%'"
        )]
        return {t: list(ref.execute(f'PRAGMA table_info("{t}")')) for t in tables}
    finally:
        ref.close()


def _add_column_sql(table, col):
    """`ALTER TABLE ... ADD COLUMN` for one `PRAGMA table_info` row, or None.

    None means SQLite cannot add this column to a populated table, so it needs a
    MIGRATIONS step instead. Two cases: a primary key (ADD COLUMN can never
    introduce one) and NOT NULL without a default (there'd be no value for the
    rows already there).
    """
    _cid, name, coltype, notnull, default, pk = col
    if pk or (notnull and default is None):
        return None
    bits = [f'"{name}"', coltype or "BLOB"]
    if notnull:
        bits.append("NOT NULL")
    if default is not None:
        # `dflt_value` is the default's literal SQL text ("0"), already quoted
        # by SQLite where it needs to be, so it interpolates as-is.
        bits.append(f"DEFAULT {default}")
    return f'ALTER TABLE "{table}" ADD COLUMN {" ".join(bits)}'


def _sync_columns(conn):
    """Add any column SCHEMA declares that this database is missing.

    This is the fix for the whole class of bug rather than one instance of it:
    `recipe_ingredient.prep` was added to SCHEMA, `CREATE TABLE IF NOT EXISTS`
    silently did nothing to the existing table, and every ingredient insert on
    the long-lived production database died with "no column named prep" while
    every test — each building a fresh database in a tmp_path — passed. Diffing
    against SCHEMA closes that gap for the next nullable column too, with no
    version bookkeeping to remember.

    Idempotent by construction: it compares against what's actually there, so
    running it on an already-current database does nothing.

    What it deliberately does NOT do, because ADD COLUMN can't: change a column's
    type or collation, add a table-level UNIQUE or FOREIGN KEY constraint, or
    drop anything. Those need a MIGRATIONS step that rebuilds the table.
    """
    added = []
    for table, cols in _declared_tables().items():
        have = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        if not have:
            continue          # table absent entirely; executescript(SCHEMA) makes it
        for col in cols:
            if col[1] in have:
                continue
            sql = _add_column_sql(table, col)
            if sql is None:
                raise SchemaDriftError(
                    f"{table}.{col[1]} is missing from this database and cannot "
                    f"be added with ALTER TABLE (primary key, or NOT NULL with "
                    f"no default). Add a MIGRATIONS step in recipes/db.py."
                )
            conn.execute(sql)
            added.append(f"{table}.{col[1]}")
    if added:
        conn.commit()
    return added


def _schema_version(conn):
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    return int(row[0]) if row else 0


def _set_schema_version(conn, version):
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


def migrate(conn):
    """Bring an existing database up to what SCHEMA declares. Safe on every boot.

    Assumes the tables exist — `init_schema` runs `executescript(SCHEMA)` first,
    which creates any table, index or trigger that's missing outright. What's
    left after that is the drift inside tables that already exist, which is what
    this handles: the ordered MIGRATIONS steps, then the automatic column sync.

    MIGRATIONS runs first so a step can create a column with constraints the
    automatic pass would refuse; the sync then only sees what's genuinely left.

    Returns the list of columns it added, for the caller to log.
    """
    with LOCK:
        start = _schema_version(conn)
        for version, (name, step) in enumerate(MIGRATIONS):
            if version < start:
                continue
            step(conn)
            _set_schema_version(conn, version + 1)
            conn.commit()
        added = _sync_columns(conn)
        # Never stamp downwards. Rolling a deploy back leaves a database ahead
        # of the code reading it; recording the older number would claim
        # migrations had been undone when they haven't, and re-run them on the
        # next deploy forward.
        _set_schema_version(conn, max(start, SCHEMA_VERSION))
        conn.commit()
    return added


def init_schema(conn):
    """Create anything missing, then migrate what's already there.

    Both halves are needed and neither substitutes for the other:
    `executescript` builds tables/indexes/triggers that don't exist at all but
    will not touch a table that does, and `migrate` fixes up the ones that do.
    """
    with LOCK:
        conn.executescript(SCHEMA)
        conn.commit()
    # Outside the block above on purpose: LOCK is a plain, non-reentrant
    # threading.Lock and migrate() takes it itself.
    return migrate(conn)


def seed_sections(conn):
    """Insert the default store layout. Idempotent — safe on every boot."""
    with LOCK:
        for s_pos, (section_name, subs) in enumerate(SECTIONS):
            conn.execute(
                "INSERT OR IGNORE INTO section(name, position) VALUES(?,?)",
                (section_name, s_pos),
            )
            sid = conn.execute(
                "SELECT id FROM section WHERE name=?", (section_name,)
            ).fetchone()["id"]
            for sub_pos, sub_name in enumerate(subs):
                conn.execute(
                    "INSERT OR IGNORE INTO subsection(section_id, name, position)"
                    " VALUES(?,?,?)",
                    (sid, sub_name, sub_pos),
                )
        conn.commit()


# The store walk, as one ordering: section position, then the invisible
# sub-category position, then alphabetical. Unfiled items (no subsection) sort
# to the very end via the COALESCE sentinel.
STORE_ORDER_SQL = """
SELECT p.*, s.name AS section_name, s.id AS section_id, sub.name AS subsection_name
FROM pantry_item p
LEFT JOIN subsection sub ON sub.id = p.subsection_id
LEFT JOIN section    s   ON s.id  = sub.section_id
ORDER BY COALESCE(s.position, 9999),
         COALESCE(sub.position, 9999),
         p.name COLLATE NOCASE
"""


def bump_version(conn):
    """Bump the list version. Every mutating request must call this."""
    with LOCK:
        conn.execute("""
            INSERT INTO meta(key, value) VALUES('list_version', '1')
            ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1
        """)
        conn.commit()
        return get_version(conn)


def get_version(conn):
    row = conn.execute(
        "SELECT value FROM meta WHERE key='list_version'"
    ).fetchone()
    return int(row["value"]) if row else 0
