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
    # deliberately inserted out of order
    _mk_item(recipes_db, "Trash bags",  "Late Aisles",             "home")
    _mk_item(recipes_db, "Gruyere",     "Produce & Fancy Cheese",  "fancy cheese")
    _mk_item(recipes_db, "Cumin",       "Early Aisles",            "spices")
    _mk_item(recipes_db, "Apples",      "Produce & Fancy Cheese",  "produce")
    _mk_item(recipes_db, "Candy bars",  "Late Aisles",             "candy")
    _mk_item(recipes_db, "Coffee",      "Early Aisles",            "coffee & tea")
    _mk_item(recipes_db, "Bananas",     "Produce & Fancy Cheese",  "produce")

    rows = recipes_db.execute(db.STORE_ORDER_SQL).fetchall()

    assert [r["name"] for r in rows] == [
        "Apples", "Bananas",   # produce, alphabetical
        "Gruyere",             # fancy cheese comes after produce
        "Coffee",              # coffee & tea precedes spices
        "Cumin",
        "Candy bars",          # candy precedes home
        "Trash bags",
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
