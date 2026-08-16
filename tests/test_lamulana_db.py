from lamulana import seed
import lamulana.db as db


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
