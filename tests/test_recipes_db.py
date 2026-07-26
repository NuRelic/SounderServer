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
    conn.execute(
        "INSERT INTO pantry_item(name, subsection_id) VALUES(?,?)", (name, sub_id)
    )
    conn.commit()


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
