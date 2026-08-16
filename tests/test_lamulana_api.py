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


def test_clue_writes_need_an_editing_session(editor_client, reader_client):
    """403 must mean "rejected", not just "nothing happened to happen".

    Writing against ids that don't exist can't tell the two apart, so create
    a real clue as the editor and prove the reader's rejected calls left it
    untouched: no second clue, no title change, still present after the
    delete attempt.
    """
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a"}
                              ).get_json()["clue"]["id"]
    assert reader_client.post("/lamulana/api/clues", json={"title": "b"}).status_code == 403
    assert reader_client.patch(f"/lamulana/api/clues/{cid}",
                                json={"title": "changed"}).status_code == 403
    assert reader_client.delete(f"/lamulana/api/clues/{cid}").status_code == 403
    got = editor_client.get("/lamulana/api/clues").get_json()["clues"]
    assert [c["title"] for c in got] == ["a"]


# --- Bad input must 400, never reach SQLite as a 500 ------------------------

def test_create_rejects_a_nonexistent_area_id(editor_client):
    r = editor_client.post("/lamulana/api/clues", json={"title": "a", "area_id": 999999})
    assert r.status_code == 400
    assert editor_client.get("/lamulana/api/clues").get_json()["clues"] == []


def test_patch_rejects_a_nonexistent_area_id(editor_client):
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a"}
                              ).get_json()["clue"]["id"]
    r = editor_client.patch(f"/lamulana/api/clues/{cid}", json={"area_id": 999999})
    assert r.status_code == 400
    clue = editor_client.get("/lamulana/api/clues").get_json()["clues"][0]
    assert clue["area_id"] is None


def test_area_id_rejects_non_integer_json_types(editor_client):
    """[] and {} used to reach sqlite3's bind and 500 with ProgrammingError."""
    for bad in ([], {}, "abc"):
        r = editor_client.post("/lamulana/api/clues", json={"title": "a", "area_id": bad})
        assert r.status_code == 400, bad
    assert editor_client.get("/lamulana/api/clues").get_json()["clues"] == []


def test_area_id_true_is_not_silently_treated_as_one(editor_client):
    """bool is an int subclass -- True used to bind as area id 1."""
    r = editor_client.post("/lamulana/api/clues", json={"title": "a", "area_id": True})
    assert r.status_code == 400
    assert editor_client.get("/lamulana/api/clues").get_json()["clues"] == []


def test_non_object_json_body_is_treated_as_empty_not_a_500(editor_client):
    r = editor_client.post("/lamulana/api/clues", data="[1, 2, 3]",
                            content_type="application/json")
    assert r.status_code == 400  # title required, not a 500 from a list's .get
    assert editor_client.get("/lamulana/api/clues").get_json()["clues"] == []


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
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a", "body": "original"}
                              ).get_json()["clue"]["id"]
    r = editor_client.patch(f"/lamulana/api/clues/{cid}", json={"body": None})
    assert r.status_code == 400
    clue = editor_client.get("/lamulana/api/clues").get_json()["clues"][0]
    assert clue["body"] == "original"


def test_create_rejects_a_non_string_title(editor_client):
    r = editor_client.post("/lamulana/api/clues", json={"title": 123})
    assert r.status_code == 400
    assert editor_client.get("/lamulana/api/clues").get_json()["clues"] == []


def test_patch_rejects_a_non_string_title(editor_client):
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a"}
                              ).get_json()["clue"]["id"]
    r = editor_client.patch(f"/lamulana/api/clues/{cid}", json={"title": 123})
    assert r.status_code == 400
    clue = editor_client.get("/lamulana/api/clues").get_json()["clues"][0]
    assert clue["title"] == "a"


# --- Filters AND together, not OR -------------------------------------------

def test_clues_area_and_state_filters_and_together(editor_client):
    ann = _area_id(editor_client, "Annwfn")
    val = _area_id(editor_client, "Valhalla")
    editor_client.post("/lamulana/api/clues", json={"title": "a", "area_id": ann})
    editor_client.post("/lamulana/api/clues", json={"title": "b", "area_id": ann,
                                                     "state": "understood"})
    editor_client.post("/lamulana/api/clues", json={"title": "c", "area_id": val,
                                                     "state": "understood"})
    got = editor_client.get(
        "/lamulana/api/clues",
        query_string={"area": ann, "state": "understood"}).get_json()["clues"]
    # If this ORed, "a" (area match) and "c" (state match) would also show up.
    assert [c["title"] for c in got] == ["b"]


# --- /api/rooms --------------------------------------------------------------

def test_rooms_lists_distinct_nonempty_names_sorted(editor_client):
    editor_client.post("/lamulana/api/clues", json={"title": "a", "room": "E-3"})
    editor_client.post("/lamulana/api/clues", json={"title": "b", "room": "E-3"})
    editor_client.post("/lamulana/api/clues", json={"title": "c", "room": "A-1"})
    editor_client.post("/lamulana/api/clues", json={"title": "d", "room": ""})
    editor_client.post("/lamulana/api/clues", json={"title": "e"})
    rooms = editor_client.get("/lamulana/api/rooms").get_json()["rooms"]
    assert rooms == ["A-1", "E-3"]


# --- Search treats %, _ and \ as literal characters, not LIKE wildcards -----

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


def test_search_backslash_is_literal_not_an_escape_character(editor_client):
    editor_client.post("/lamulana/api/clues", json={"title": "no punctuation here"})
    editor_client.post("/lamulana/api/clues", json={"title": r"back\slash"})
    got = editor_client.get("/lamulana/api/clues",
                             query_string={"q": r"back\slash"}).get_json()["clues"]
    assert [c["title"] for c in got] == ["back\\slash"]


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


# --- Bad input must 400, never reach SQLite as a 500 (same gap Task 4 found
# and fixed for clues -- see _clean_body) ------------------------------------

def test_thread_create_rejects_a_nonexistent_area_id(editor_client):
    r = editor_client.post("/lamulana/api/threads", json={"title": "a", "area_id": 999999})
    assert r.status_code == 400
    assert editor_client.get("/lamulana/api/threads").get_json()["threads"] == []


def test_thread_patch_rejects_a_nonexistent_area_id(editor_client):
    tid = editor_client.post("/lamulana/api/threads", json={"title": "a"}
                              ).get_json()["thread"]["id"]
    r = editor_client.patch(f"/lamulana/api/threads/{tid}", json={"area_id": 999999})
    assert r.status_code == 400
    thread = editor_client.get("/lamulana/api/threads").get_json()["threads"][0]
    assert thread["area_id"] is None


def test_thread_create_rejects_a_non_string_title(editor_client):
    r = editor_client.post("/lamulana/api/threads", json={"title": 123})
    assert r.status_code == 400
    assert editor_client.get("/lamulana/api/threads").get_json()["threads"] == []


def test_thread_patch_rejects_a_non_string_title(editor_client):
    tid = editor_client.post("/lamulana/api/threads", json={"title": "a"}
                              ).get_json()["thread"]["id"]
    r = editor_client.patch(f"/lamulana/api/threads/{tid}", json={"title": 123})
    assert r.status_code == 400
    thread = editor_client.get("/lamulana/api/threads").get_json()["threads"][0]
    assert thread["title"] == "a"
