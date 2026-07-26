import sqlite3

import pytest

import recipes.db as db


def test_seeds_six_sections_in_walking_order(recipes_db):
    rows = recipes_db.execute(
        "SELECT name FROM section ORDER BY position"
    ).fetchall()
    assert [r["name"] for r in rows] == [
        "Produce & Fancy Cheese",
        "Early Aisles",
        "Middle Aisles",
        "Late Aisles",
        "Freezer / Dairy / Bread",
        "Unsorted",
    ]


def test_unsorted_is_always_last(recipes_db):
    positions = dict(recipes_db.execute(
        "SELECT name, position FROM section"
    ).fetchall())
    assert positions["Unsorted"] == max(positions.values())


def test_subsections_keep_their_declared_order(recipes_db):
    rows = recipes_db.execute("""
        SELECT sub.name FROM subsection sub
        JOIN section s ON s.id = sub.section_id
        WHERE s.name = 'Produce & Fancy Cheese'
        ORDER BY sub.position
    """).fetchall()
    assert [r["name"] for r in rows] == ["produce", "fancy cheese"]


def test_seeding_is_idempotent(recipes_db):
    before = recipes_db.execute("SELECT COUNT(*) c FROM section").fetchone()["c"]
    db.seed_sections(recipes_db)
    after = recipes_db.execute("SELECT COUNT(*) c FROM section").fetchone()["c"]
    assert before == after


def _mk_item(conn, name, section, sub):
    sub_id = conn.execute("""
        SELECT sub.id FROM subsection sub JOIN section s ON s.id = sub.section_id
        WHERE s.name=? AND sub.name=?
    """, (section, sub)).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO pantry_item(name, subsection_id) VALUES(?,?)", (name, sub_id)
    )
    conn.commit()
    return cur.lastrowid


def _mk_pantry_item(conn, name):
    cur = conn.execute("INSERT INTO pantry_item(name) VALUES(?)", (name,))
    conn.commit()
    return cur.lastrowid


def _mk_recipe(conn, name):
    cur = conn.execute("INSERT INTO recipe(name, created_at) VALUES(?, 0)", (name,))
    conn.commit()
    return cur.lastrowid


def _mk_line(conn, pantry_item_id=None, free_text=None, checked=0):
    cur = conn.execute(
        "INSERT INTO list_line(pantry_item_id, free_text, checked, created_at)"
        " VALUES(?,?,?,0)",
        (pantry_item_id, free_text, checked),
    )
    conn.commit()
    return cur.lastrowid


def _mk_contribution(conn, list_line_id, recipe_id, qty=None):
    cur = conn.execute(
        "INSERT INTO list_contribution(list_line_id, recipe_id, qty) VALUES(?,?,?)",
        (list_line_id, recipe_id, qty),
    )
    conn.commit()
    return cur.lastrowid


def test_store_order_is_section_then_subsection_then_alphabetical(recipes_db):
    # Names are deliberately picked to fight their subcategory: in each pair
    # below, the item filed in the *later* subcategory sorts alphabetically
    # *before* the other one. That means dropping the subsection sort key
    # (leaving only section + alphabetical) would flip these pairs, so a
    # regression there causes a real assertion failure, not a coincidental
    # pass.
    _mk_item(recipes_db, "Zucchini",    "Produce & Fancy Cheese",  "produce")       # pos 0
    _mk_item(recipes_db, "Apples",      "Produce & Fancy Cheese",  "produce")       # pos 0, alpha check
    _mk_item(recipes_db, "Brie",        "Produce & Fancy Cheese",  "fancy cheese")  # pos 1, sorts < Zucchini
    _mk_item(recipes_db, "Walnuts",     "Early Aisles",            "shelf-stable fruit")  # pos 0
    _mk_item(recipes_db, "Almonds",     "Early Aisles",            "baking")              # pos 5, sorts < Walnuts
    _mk_item(recipes_db, "Zephyr gum",  "Late Aisles",             "candy")          # pos 0
    _mk_item(recipes_db, "Aloe wipes",  "Late Aisles",             "toiletries")     # pos 5, sorts < Zephyr gum

    rows = recipes_db.execute(db.STORE_ORDER_SQL).fetchall()

    assert [r["name"] for r in rows] == [
        "Apples", "Zucchini",  # produce (pos 0), alphabetical within the subcategory
        "Brie",                # fancy cheese (pos 1) — after produce despite B < Z
        "Walnuts",             # shelf-stable fruit (pos 0) — before baking despite W > A
        "Almonds",             # baking (pos 5)
        "Zephyr gum",          # candy (pos 0) — before toiletries despite Z > A
        "Aloe wipes",          # toiletries (pos 5)
    ]


def test_unfiled_items_sort_last(recipes_db):
    _mk_item(recipes_db, "Apples", "Produce & Fancy Cheese", "produce")
    recipes_db.execute("INSERT INTO pantry_item(name) VALUES('Mystery powder')")
    recipes_db.commit()
    rows = recipes_db.execute(db.STORE_ORDER_SQL).fetchall()
    assert rows[-1]["name"] == "Mystery powder"


def test_version_starts_at_zero_and_increases(recipes_db):
    assert db.get_version(recipes_db) == 0
    assert db.bump_version(recipes_db) == 1
    assert db.bump_version(recipes_db) == 2
    assert db.get_version(recipes_db) == 2


def test_connect_enables_foreign_keys(recipes_db):
    # If this PRAGMA is ever dropped, every ON DELETE CASCADE/SET NULL below
    # silently becomes a no-op and the rest of this file would go on passing
    # for the wrong reason.
    row = recipes_db.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_pantry_item_name_uniqueness_is_case_insensitive(recipes_db):
    _mk_pantry_item(recipes_db, "Onions")
    with pytest.raises(sqlite3.IntegrityError):
        recipes_db.execute("INSERT INTO pantry_item(name) VALUES('onions')")
    recipes_db.rollback()


def test_subsection_name_uniqueness_is_case_insensitive(recipes_db):
    section_id = recipes_db.execute(
        "SELECT id FROM section WHERE name='Produce & Fancy Cheese'"
    ).fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError):
        recipes_db.execute(
            "INSERT INTO subsection(section_id, name, position) VALUES(?, 'PRODUCE', 99)",
            (section_id,),
        )
    recipes_db.rollback()


def test_pantry_alias_uniqueness_is_case_insensitive(recipes_db):
    item_id = _mk_pantry_item(recipes_db, "Tomato")
    recipes_db.execute(
        "INSERT INTO pantry_alias(pantry_item_id, alias) VALUES(?, 'Tomatoes')",
        (item_id,),
    )
    recipes_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        recipes_db.execute(
            "INSERT INTO pantry_alias(pantry_item_id, alias) VALUES(?, 'tomatoes')",
            (item_id,),
        )
    recipes_db.rollback()


def test_two_contributions_attach_to_one_line(recipes_db):
    item_id = _mk_pantry_item(recipes_db, "Onions")
    soup = _mk_recipe(recipes_db, "Soup")
    stew = _mk_recipe(recipes_db, "Stew")
    line_id = _mk_line(recipes_db, pantry_item_id=item_id)
    _mk_contribution(recipes_db, line_id, soup, qty=1)
    _mk_contribution(recipes_db, line_id, stew, qty=2)

    line_count = recipes_db.execute(
        "SELECT COUNT(*) c FROM list_line WHERE pantry_item_id=?", (item_id,)
    ).fetchone()["c"]
    contributions = recipes_db.execute(
        "SELECT recipe_id, qty FROM list_contribution WHERE list_line_id=? ORDER BY qty",
        (line_id,),
    ).fetchall()

    assert line_count == 1
    assert [(r["recipe_id"], r["qty"]) for r in contributions] == [(soup, 1), (stew, 2)]


def test_deleting_one_contribution_leaves_the_others_share_intact(recipes_db):
    item_id = _mk_pantry_item(recipes_db, "Garlic")
    soup = _mk_recipe(recipes_db, "Soup")
    stew = _mk_recipe(recipes_db, "Stew")
    line_id = _mk_line(recipes_db, pantry_item_id=item_id)
    soup_contribution = _mk_contribution(recipes_db, line_id, soup, qty=1)
    _mk_contribution(recipes_db, line_id, stew, qty=2)

    recipes_db.execute(
        "DELETE FROM list_contribution WHERE id=?", (soup_contribution,)
    )
    recipes_db.commit()

    remaining = recipes_db.execute(
        "SELECT recipe_id, qty FROM list_contribution WHERE list_line_id=?", (line_id,)
    ).fetchall()
    assert [(r["recipe_id"], r["qty"]) for r in remaining] == [(stew, 2)]
    # the line itself still stands -- stew's claim is still open
    assert recipes_db.execute(
        "SELECT id FROM list_line WHERE id=?", (line_id,)
    ).fetchone() is not None


def test_deleting_a_recipe_cascades_contributions_and_drops_orphaned_line(recipes_db):
    item_id = _mk_pantry_item(recipes_db, "Basil")
    soup = _mk_recipe(recipes_db, "Soup")
    line_id = _mk_line(recipes_db, pantry_item_id=item_id)
    _mk_contribution(recipes_db, line_id, soup, qty=1)

    recipes_db.execute("DELETE FROM recipe WHERE id=?", (soup,))
    recipes_db.commit()

    remaining_contributions = recipes_db.execute(
        "SELECT COUNT(*) c FROM list_contribution WHERE recipe_id=?", (soup,)
    ).fetchone()["c"]
    line_still_there = recipes_db.execute(
        "SELECT id FROM list_line WHERE id=?", (line_id,)
    ).fetchone()

    assert remaining_contributions == 0
    assert line_still_there is None  # nothing wants it anymore -- the trigger dropped it


def test_open_line_is_unique_per_pantry_item(recipes_db):
    item_id = _mk_pantry_item(recipes_db, "Milk")
    _mk_line(recipes_db, pantry_item_id=item_id)  # first open line for Milk

    with pytest.raises(sqlite3.IntegrityError):
        _mk_line(recipes_db, pantry_item_id=item_id)  # a second one collides
    recipes_db.rollback()

    # the index is partial (WHERE checked = 0): a *checked* line for the same
    # item doesn't collide with the still-open one
    _mk_line(recipes_db, pantry_item_id=item_id, checked=1)
