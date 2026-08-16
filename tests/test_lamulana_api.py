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
