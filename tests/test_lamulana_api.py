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


# --- Bad input must 400, never reach SQLite as a 500 ------------------------

def test_create_rejects_a_nonexistent_area_id(editor_client):
    r = editor_client.post("/lamulana/api/clues", json={"title": "a", "area_id": 999999})
    assert r.status_code == 400


def test_patch_rejects_a_nonexistent_area_id(editor_client):
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a"}
                              ).get_json()["clue"]["id"]
    r = editor_client.patch(f"/lamulana/api/clues/{cid}", json={"area_id": 999999})
    assert r.status_code == 400


def test_patch_empty_string_area_id_clears_the_area(editor_client):
    """An empty <select> posts "", which means "no area", not a bad id."""
    area = _area_id(editor_client, "Annwfn")
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a", "area_id": area}
                              ).get_json()["clue"]["id"]
    r = editor_client.patch(f"/lamulana/api/clues/{cid}", json={"area_id": ""})
    assert r.status_code == 200
    clue = r.get_json()["clue"]
    assert clue["area_id"] is None
    assert clue["area"] is None


def test_a_valid_area_id_still_works(editor_client):
    area = _area_id(editor_client, "Valhalla")
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a"}
                              ).get_json()["clue"]["id"]
    r = editor_client.patch(f"/lamulana/api/clues/{cid}", json={"area_id": area})
    assert r.status_code == 200
    clue = r.get_json()["clue"]
    assert clue["area_id"] == area
    assert clue["area"] == "Valhalla"


def test_patch_rejects_a_null_body(editor_client):
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a"}
                              ).get_json()["clue"]["id"]
    r = editor_client.patch(f"/lamulana/api/clues/{cid}", json={"body": None})
    assert r.status_code == 400


def test_create_rejects_a_non_string_title(editor_client):
    r = editor_client.post("/lamulana/api/clues", json={"title": 123})
    assert r.status_code == 400


def test_patch_rejects_a_non_string_title(editor_client):
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a"}
                              ).get_json()["clue"]["id"]
    r = editor_client.patch(f"/lamulana/api/clues/{cid}", json={"title": 123})
    assert r.status_code == 400


# --- Search treats % and _ as literal characters, not LIKE wildcards --------

def test_search_percent_is_literal_not_a_wildcard(editor_client):
    editor_client.post("/lamulana/api/clues", json={"title": "no punctuation here"})
    editor_client.post("/lamulana/api/clues", json={"title": "100% done"})
    got = editor_client.get("/lamulana/api/clues",
                             query_string={"q": "100%"}).get_json()["clues"]
    assert [c["title"] for c in got] == ["100% done"]


def test_search_underscore_is_literal_not_a_wildcard(editor_client):
    editor_client.post("/lamulana/api/clues", json={"title": "no punctuation here"})
    editor_client.post("/lamulana/api/clues", json={"title": "a_b marker"})
    got = editor_client.get("/lamulana/api/clues",
                             query_string={"q": "_"}).get_json()["clues"]
    assert [c["title"] for c in got] == ["a_b marker"]
