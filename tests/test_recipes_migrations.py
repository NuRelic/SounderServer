"""Migrating a database that already exists.

Every other test in this suite builds its database fresh in a `tmp_path`, which
is exactly the blind spot that let the `prep` bug reach production: `CREATE
TABLE IF NOT EXISTS` cannot add a column to a table that is already there, so a
long-lived database kept the old `recipe_ingredient` and every ingredient insert
died with `sqlite3.OperationalError: table recipe_ingredient has no column named
prep`. A fresh database proves nothing about that. These tests all start from a
database built with an OLD schema and then migrate it.
"""

import importlib
import re
import sqlite3
import sys
import threading

import flask
import pytest

from recipes import db


def _old_schema_without_prep():
    """Today's SCHEMA with the `prep` column removed — the pre-Task-7 shape.

    Derived from the live SCHEMA rather than pasted as a historical copy so it
    cannot rot: if the column is ever renamed the substitution stops matching
    and the assert below fails loudly instead of silently testing nothing.
    """
    old, n = re.subn(r"^ *prep +TEXT,\n", "", db.SCHEMA, flags=re.M)
    assert n == 1, "expected to strip exactly one `prep` column from SCHEMA"
    return old


def _old_database(path, schema=None):
    """A database on disk built with the old schema and carrying real rows."""
    # check_same_thread=False mirrors what `db.connect` does in production, so
    # the deadlock test below can drive migration from a worker thread.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(schema or _old_schema_without_prep())
    conn.execute(
        "INSERT INTO recipe(id, name, created_at) VALUES(1, 'Old recipe', 0)")
    conn.execute("INSERT INTO pantry_item(id, name) VALUES(1, 'Onions')")
    conn.execute(
        "INSERT INTO recipe_ingredient(recipe_id, position, raw_text, qty, unit)"
        " VALUES(1, 0, '2 onions', 2, NULL)")
    conn.commit()
    return conn


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _objects(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}


# Everything the schema grew after the very first release, per
# `git log -p --follow recipes/db.py`: one column, three indexes, one trigger.
# Stripped out of the live SCHEMA rather than pasted as a historical copy so a
# rename fails the substitution below instead of silently testing nothing.
_LATER_ADDITIONS = [
    (r"^ *prep +TEXT,\n", "recipe_ingredient.prep"),
    (r"^CREATE INDEX IF NOT EXISTS idx_alias_pantry[^;]*;\n", "idx_alias_pantry"),
    (r"^CREATE UNIQUE INDEX IF NOT EXISTS idx_line_open_item[^;]*;\n", "idx_line_open_item"),
    (r"^CREATE UNIQUE INDEX IF NOT EXISTS idx_line_open_text[^;]*;\n", "idx_line_open_text"),
    (r"^CREATE TRIGGER IF NOT EXISTS trg_drop_childless_line.*?\nEND;\n", "trg_drop_childless_line"),
]


# The same commit that added the indexes and the trigger also put COLLATE
# NOCASE on the three case-insensitive uniqueness rules. Unlike the additions
# above, no ALTER TABLE can retrofit a collation, so this is the one piece of
# history a first-release database does NOT get back — see the note beside
# MIGRATIONS in recipes/db.py, and the test that pins it below.
_LATER_COLLATIONS = [
    (r"UNIQUE\(section_id, name COLLATE NOCASE\)", "UNIQUE(section_id, name)"),
    (r"name +TEXT NOT NULL COLLATE NOCASE UNIQUE", "name           TEXT NOT NULL UNIQUE"),
    (r"alias +TEXT NOT NULL COLLATE NOCASE UNIQUE", "alias          TEXT NOT NULL UNIQUE"),
]


def _first_release_schema():
    """SCHEMA as it stood at the first commit: before every later change."""
    sql = db.SCHEMA
    for pattern, what in _LATER_ADDITIONS:
        sql, n = re.subn(pattern, "", sql, flags=re.M | re.S)
        assert n == 1, f"expected to strip exactly one {what} from SCHEMA"
    for pattern, replacement in _LATER_COLLATIONS:
        sql, n = re.subn(pattern, replacement, sql)
        assert n == 1, f"expected to un-collate exactly one {pattern!r}"
    return sql


def test_the_oldest_database_gains_every_later_index_and_trigger(tmp_path):
    """Not just `prep` — the whole backlog of schema history.

    Indexes and the trigger carry `IF NOT EXISTS`, so `executescript(SCHEMA)`
    should already create them on an old database. "Should" is the word that got
    us here, so this asserts it instead.
    """
    conn = _old_database(tmp_path / "oldest.db", schema=_first_release_schema())
    before = _objects(conn)
    for _pattern, what in _LATER_ADDITIONS[1:]:
        assert what not in before, f"fixture should not already have {what}"

    db.init_schema(conn)

    assert "prep" in _columns(conn, "recipe_ingredient")
    for _pattern, what in _LATER_ADDITIONS[1:]:
        assert what in _objects(conn), f"{what} was never created"

    # The trigger is the one that has to actually fire, not merely exist.
    conn.execute("INSERT INTO list_line(id, pantry_item_id, created_at)"
                 " VALUES(1, 1, 0)")
    conn.execute("INSERT INTO list_contribution(id, list_line_id, recipe_id)"
                 " VALUES(1, 1, 1)")
    conn.commit()
    conn.execute("DELETE FROM list_contribution WHERE id=1")
    conn.commit()
    assert conn.execute("SELECT id FROM list_line WHERE id=1").fetchone() is None


def test_the_collation_drift_is_a_known_unfixed_limit(tmp_path):
    """Documents the one drift the migration deliberately does not repair.

    The first schema lacked COLLATE NOCASE on `pantry_item.name`, and
    `ALTER TABLE ADD COLUMN` cannot change a column's collation — only a full
    table rebuild can. No reachable database is in that state (nothing creates
    recipes.db but recipes/api.py, written after the collations landed), so
    db.py carries the reasoning instead of the rebuild. If this test ever starts
    failing, someone added the rebuild and should delete this test.
    """
    conn = _old_database(tmp_path / "oldest.db", schema=_first_release_schema())

    db.init_schema(conn)

    conn.execute("INSERT INTO pantry_item(name) VALUES('Shallots')")
    conn.execute("INSERT INTO pantry_item(name) VALUES('shallots')")  # not rejected
    conn.rollback()


def test_the_old_database_really_is_missing_prep(tmp_path):
    """Guard on the fixture itself — otherwise the tests below prove nothing."""
    conn = _old_database(tmp_path / "old.db")
    assert "prep" not in _columns(conn, "recipe_ingredient")


def test_migrating_an_old_database_adds_prep_and_inserts_work(tmp_path):
    """The production failure, reproduced and then fixed.

    Without the migration the INSERT below raises OperationalError: table
    recipe_ingredient has no column named prep.
    """
    conn = _old_database(tmp_path / "old.db")

    db.init_schema(conn)

    assert "prep" in _columns(conn, "recipe_ingredient")
    conn.execute(
        "INSERT INTO recipe_ingredient"
        "(recipe_id, position, raw_text, qty, unit, prep, pantry_item_id)"
        " VALUES(1, 1, '3 carrots, diced', 3, NULL, 'diced', 1)")
    conn.commit()
    row = conn.execute(
        "SELECT prep FROM recipe_ingredient WHERE position=1").fetchone()
    assert row["prep"] == "diced"


def test_migration_preserves_the_rows_already_there(tmp_path):
    """A migration that loses the family's data is worse than the bug."""
    conn = _old_database(tmp_path / "old.db")

    db.init_schema(conn)

    assert conn.execute("SELECT name FROM recipe WHERE id=1").fetchone()[0] == "Old recipe"
    assert conn.execute("SELECT name FROM pantry_item WHERE id=1").fetchone()[0] == "Onions"
    old = conn.execute(
        "SELECT raw_text, qty, prep FROM recipe_ingredient WHERE position=0").fetchone()
    assert old["raw_text"] == "2 onions"
    assert old["qty"] == 2
    assert old["prep"] is None      # backfilled as NULL, not invented


def test_migration_is_idempotent_across_repeated_boots(tmp_path):
    """It runs on every startup, so running it twice must be a no-op."""
    conn = _old_database(tmp_path / "old.db")

    first = db.init_schema(conn)
    assert "recipe_ingredient.prep" in first

    for _ in range(3):
        assert db.init_schema(conn) == [], "a second boot should add nothing"
    assert _columns(conn, "recipe_ingredient").count("prep") == 1


def test_a_brand_new_database_needs_no_migrating(tmp_path):
    conn = db.connect(str(tmp_path / "new.db"))

    added = db.init_schema(conn)

    assert added == []
    assert "prep" in _columns(conn, "recipe_ingredient")
    assert db._schema_version(conn) == db.SCHEMA_VERSION


def test_migration_stamps_the_schema_version(tmp_path):
    conn = _old_database(tmp_path / "old.db")
    assert db._schema_version(conn) == 0, "an unstamped database reads as 0"

    db.init_schema(conn)

    assert db._schema_version(conn) == db.SCHEMA_VERSION


def test_the_version_stamp_does_not_disturb_the_list_version(tmp_path):
    """Both live in `meta`; the sync poll's version must survive migrating."""
    conn = _old_database(tmp_path / "old.db")
    conn.execute("INSERT INTO meta(key, value) VALUES('list_version', '7')")
    conn.commit()

    db.init_schema(conn)

    assert db.get_version(conn) == 7


def test_any_missing_nullable_column_is_healed_not_just_prep(tmp_path):
    """The general case — this is what stops the next instance of this bug.

    Drops a different column from a different table and checks the sync notices.
    """
    mangled, n = re.subn(r"^ *shaws_sku +TEXT,\n", "", db.SCHEMA, flags=re.M)
    assert n == 1
    conn = _old_database(tmp_path / "old.db", schema=mangled)
    assert "shaws_sku" not in _columns(conn, "pantry_item")

    added = db.init_schema(conn)

    assert "pantry_item.shaws_sku" in added
    assert "shaws_sku" in _columns(conn, "pantry_item")


def test_a_missing_column_with_a_default_keeps_its_default(tmp_path):
    """`is_staple INTEGER NOT NULL DEFAULT 0` — NOT NULL is addable *with* a default."""
    mangled, n = re.subn(r"^ *is_staple +INTEGER NOT NULL DEFAULT 0,\n", "",
                         db.SCHEMA, flags=re.M)
    assert n == 1
    conn = _old_database(tmp_path / "old.db", schema=mangled)

    added = db.init_schema(conn)

    assert "pantry_item.is_staple" in added
    # The row inserted before the migration must have picked up the default.
    assert conn.execute("SELECT is_staple FROM pantry_item WHERE id=1").fetchone()[0] == 0


def test_a_column_alter_table_cannot_add_raises_a_clear_error(tmp_path, monkeypatch):
    """The documented limit of the automatic path, asserted rather than assumed.

    SQLite cannot ADD COLUMN a NOT NULL column with no default: there is no
    value for the rows already in the table. That must fail loudly at startup
    pointing at MIGRATIONS, not silently leave the column missing.
    """
    conn = _old_database(tmp_path / "old.db")
    monkeypatch.setattr(
        db, "SCHEMA",
        db.SCHEMA.replace("    notes          TEXT\n",
                          "    notes          TEXT,\n"
                          "    must_have      TEXT NOT NULL\n"))

    with pytest.raises(db.SchemaDriftError) as caught:
        db.init_schema(conn)
    assert "pantry_item.must_have" in str(caught.value)
    assert "MIGRATIONS" in str(caught.value)


def test_ordered_migration_steps_run_once_in_order(tmp_path, monkeypatch):
    """MIGRATIONS is empty today, so exercise the machinery with fake steps."""
    conn = _old_database(tmp_path / "old.db")
    ran = []
    steps = [("first", lambda c: ran.append("first")),
             ("second", lambda c: ran.append("second"))]
    monkeypatch.setattr(db, "MIGRATIONS", steps)
    monkeypatch.setattr(db, "SCHEMA_VERSION", len(steps))

    db.init_schema(conn)
    assert ran == ["first", "second"]

    db.init_schema(conn)
    assert ran == ["first", "second"], "already-applied steps must not re-run"
    assert db._schema_version(conn) == 2


def test_migration_does_not_deadlock_on_the_write_lock(tmp_path):
    """`LOCK` is not reentrant; init_schema -> migrate must not nest it.

    Run on a worker thread with a join timeout so a nested acquire fails this
    test instead of hanging the whole suite — a deadlock's signature is work
    that never finishes, which a plain call here could not distinguish.
    """
    conn = _old_database(tmp_path / "old.db")
    done = []

    worker = threading.Thread(target=lambda: done.append(db.init_schema(conn)))
    worker.daemon = True
    worker.start()
    worker.join(timeout=20)

    assert not worker.is_alive(), "init_schema deadlocked on the non-reentrant LOCK"
    assert done and "recipe_ingredient.prep" in done[0]
    assert not db.LOCK.locked(), "LOCK must be released after migrating"


def _scrub_recipes_modules():
    for mod in [m for m in list(sys.modules)
                if m == "recipes" or m.startswith("recipes.")]:
        del sys.modules[mod]


def test_the_real_app_can_save_a_recipe_against_a_migrated_database(
        tmp_path, monkeypatch):
    """End-to-end: the exact request that was 500ing in production.

    Goes through the blueprint rather than raw SQL, because the production
    traceback came from `_store_ingredients`, not from a test's own INSERT.
    """
    _old_database(tmp_path / "recipes.db").close()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    # Re-import the blueprint so it binds to the old database above; scrub again
    # on the way out so the next test doesn't inherit this DATA_DIR.
    _scrub_recipes_modules()
    try:
        api = importlib.import_module("recipes.api")   # runs init_schema on import

        app = flask.Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(api.bp)
        client = app.test_client()
        with client.session_transaction() as s:
            s["can_edit"] = True

        resp = client.post("/recipes/api/recipes", json={
            "name": "Migrated pasta", "who": "test",
            "ingredients": ["2 onions, diced", "1 lb tomatoes"]})

        assert resp.status_code == 200, resp.get_data(as_text=True)
        ingredients = resp.get_json()["ingredients"]
        assert [i["prep"] for i in ingredients] == ["diced", ""]
    finally:
        _scrub_recipes_modules()
