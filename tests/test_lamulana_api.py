import importlib
import itertools
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


def test_create_thread_keeps_the_solution_field(editor_client):
    """_clean_body type-checks "solution" via THREAD_FIELDS, so a bad value
    400s -- but the INSERT column list has to actually include the column,
    or a valid value silently comes back null. This is that round trip.
    """
    r = editor_client.post("/lamulana/api/threads",
                            json={"title": "a", "solution": "ring the four bells"})
    assert r.get_json()["thread"]["solution"] == "ring the four bells"


def test_thread_detail_has_no_clues_yet(editor_client):
    tid = editor_client.post("/lamulana/api/threads", json={"title": "t"}
                              ).get_json()["thread"]["id"]
    r = editor_client.get(f"/lamulana/api/threads/{tid}")
    assert r.get_json()["thread"]["clues"] == []


def test_thread_detail_404s_when_missing(editor_client):
    assert editor_client.get("/lamulana/api/threads/9999").status_code == 404


def test_thread_detail_inlines_linked_clues(editor_client):
    """The headline feature of this commit, exercised with real rows.

    Linking doesn't exist until Task 6, so the clue_thread rows are inserted
    directly, the way test_bootstrap_counts_distinguish_their_sources inserts
    straight into clue/thread above. This is what proves the appended JOIN in
    api_thread_detail composes with CLUE_SELECT's own LEFT JOIN area rather
    than colliding with it, that clue_count matches the linked rows, and that
    a clue linked to a *different* thread does not leak in.
    """
    conn = sys.modules["lamulana.api"]._conn()
    area = _area_id(editor_client, "Immortal Battlefield")
    tid = editor_client.post("/lamulana/api/threads", json={"title": "t"}
                              ).get_json()["thread"]["id"]
    other_tid = editor_client.post("/lamulana/api/threads", json={"title": "other"}
                                    ).get_json()["thread"]["id"]
    cid1 = editor_client.post("/lamulana/api/clues", json={
        "title": "clue one", "area_id": area, "state": "understood"},
    ).get_json()["clue"]["id"]
    cid2 = editor_client.post("/lamulana/api/clues", json={"title": "clue two"}
                               ).get_json()["clue"]["id"]
    other_cid = editor_client.post("/lamulana/api/clues", json={"title": "unrelated clue"}
                                    ).get_json()["clue"]["id"]
    conn.execute("INSERT INTO clue_thread (clue_id, thread_id) VALUES (?, ?)", (cid1, tid))
    conn.execute("INSERT INTO clue_thread (clue_id, thread_id) VALUES (?, ?)", (cid2, tid))
    conn.execute("INSERT INTO clue_thread (clue_id, thread_id) VALUES (?, ?)",
                 (other_cid, other_tid))
    conn.commit()

    thread = editor_client.get(f"/lamulana/api/threads/{tid}").get_json()["thread"]
    assert thread["clue_count"] == len(thread["clues"]) == 2
    by_id = {c["id"]: c for c in thread["clues"]}
    assert set(by_id) == {cid1, cid2}
    assert other_cid not in by_id
    # LEFT JOIN area (from CLUE_SELECT) still resolves through the appended
    # INNER JOIN clue_thread rather than being shadowed by it.
    assert by_id[cid1]["area"] == "Immortal Battlefield"


def test_threads_filter_by_state(editor_client):
    editor_client.post("/lamulana/api/threads", json={"title": "open one"})
    tid = editor_client.post("/lamulana/api/threads", json={"title": "done one"}
                              ).get_json()["thread"]["id"]
    editor_client.patch(f"/lamulana/api/threads/{tid}", json={"state": "solved"})
    got = editor_client.get("/lamulana/api/threads?state=open").get_json()["threads"]
    assert [t["title"] for t in got] == ["open one"]


def test_threads_order_open_first_then_most_recently_touched(editor_client):
    """Pins updated_at by hand so ordering doesn't depend on wall-clock timing."""
    conn = sys.modules["lamulana.api"]._conn()
    a = editor_client.post("/lamulana/api/threads", json={"title": "solved recent"}
                            ).get_json()["thread"]["id"]
    b = editor_client.post("/lamulana/api/threads", json={"title": "open old"}
                            ).get_json()["thread"]["id"]
    c = editor_client.post("/lamulana/api/threads", json={"title": "open new"}
                            ).get_json()["thread"]["id"]
    conn.execute("UPDATE thread SET state = 'solved', updated_at = 100 WHERE id = ?", (a,))
    conn.execute("UPDATE thread SET state = 'open', updated_at = 10 WHERE id = ?", (b,))
    conn.execute("UPDATE thread SET state = 'open', updated_at = 50 WHERE id = ?", (c,))
    conn.commit()
    got = editor_client.get("/lamulana/api/threads").get_json()["threads"]
    assert [t["title"] for t in got] == ["open new", "open old", "solved recent"]


def test_solved_at_bookkeeping(editor_client, monkeypatch):
    """Every call below lands in the same wall-clock second in practice, which
    would let a broken guard (or a dropped "AND solved_at IS NULL") pass by
    accident -- _now() returning the same integer twice either way. Fake it
    to hand out a new value on every call instead, so "unchanged" and "fresh"
    below are both real assertions about which branch ran, not artifacts of
    clock resolution.
    """
    api = sys.modules["lamulana.api"]
    counter = itertools.count(1000, 1000)
    monkeypatch.setattr(api, "_now", lambda: next(counter))

    tid = editor_client.post("/lamulana/api/threads", json={"title": "a"}
                              ).get_json()["thread"]["id"]
    thread = editor_client.get(f"/lamulana/api/threads/{tid}").get_json()["thread"]
    assert thread["solved_at"] is None

    t = editor_client.patch(f"/lamulana/api/threads/{tid}",
                             json={"state": "solved"}).get_json()["thread"]
    assert t["state"] == "solved"
    first_solved_at = t["solved_at"]
    assert first_solved_at is not None

    # A second PATCH to "solved" must not stomp the original timestamp -- the
    # "AND solved_at IS NULL" guard in api_thread_patch is what makes this
    # true. Each PATCH below burns a fresh, distinct value off the counter,
    # so if the guard were deleted this would come back different and fail.
    t = editor_client.patch(f"/lamulana/api/threads/{tid}",
                             json={"state": "solved"}).get_json()["thread"]
    assert t["solved_at"] == first_solved_at

    # An unrelated field edit must leave solved_at alone.
    t = editor_client.patch(f"/lamulana/api/threads/{tid}",
                             json={"body": "unrelated edit"}).get_json()["thread"]
    assert t["solved_at"] == first_solved_at

    # Reopening clears it.
    t = editor_client.patch(f"/lamulana/api/threads/{tid}",
                             json={"state": "open"}).get_json()["thread"]
    assert t["state"] == "open"
    assert t["solved_at"] is None

    # Re-solving after a reopen sets a fresh timestamp, distinct from the
    # first one, rather than leaving it null or silently refusing to re-solve.
    t = editor_client.patch(f"/lamulana/api/threads/{tid}",
                             json={"state": "solved"}).get_json()["thread"]
    assert t["solved_at"] is not None
    assert t["solved_at"] != first_solved_at


def test_thread_writes_need_an_editing_session(editor_client, reader_client):
    """403 must mean "rejected", not just "nothing happened to happen".

    Thread id 1 may not exist in a fresh test database, so a 403 there is
    indistinguishable from "there was nothing to change" -- the same gap
    test_clue_writes_need_an_editing_session above was rewritten to close.
    Create a real thread as the editor and prove the reader's rejected calls
    left it untouched.
    """
    tid = editor_client.post("/lamulana/api/threads", json={"title": "a"}
                              ).get_json()["thread"]["id"]
    assert reader_client.post("/lamulana/api/threads", json={"title": "b"}).status_code == 403
    assert reader_client.patch(f"/lamulana/api/threads/{tid}",
                                json={"title": "changed"}).status_code == 403
    assert reader_client.delete(f"/lamulana/api/threads/{tid}").status_code == 403
    got = editor_client.get("/lamulana/api/threads").get_json()["threads"]
    assert [t["title"] for t in got] == ["a"]


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


def _clue_and_thread(client):
    cid = client.post("/lamulana/api/clues", json={
        "title": "twin serpents", "body": "where the twin serpents meet",
        "state": "understood"}).get_json()["clue"]["id"]
    tid = client.post("/lamulana/api/threads", json={"title": "ankh won't spawn"}
                      ).get_json()["thread"]["id"]
    return cid, tid


def test_link_shows_on_both_sides(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    assert editor_client.post("/lamulana/api/link",
                               json={"clue_id": cid, "thread_id": tid}).status_code == 200
    detail = editor_client.get(f"/lamulana/api/threads/{tid}").get_json()["thread"]
    assert [c["id"] for c in detail["clues"]] == [cid]
    assert detail["clue_count"] == 1
    clue = editor_client.get("/lamulana/api/clues").get_json()["clues"][0]
    assert [t["id"] for t in clue["threads"]] == [tid]


def test_linking_twice_is_harmless(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    r = editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    assert r.status_code == 200
    detail = editor_client.get(f"/lamulana/api/threads/{tid}").get_json()["thread"]
    assert len(detail["clues"]) == 1


def test_unlink(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    assert editor_client.delete("/lamulana/api/link",
                                 json={"clue_id": cid, "thread_id": tid}).status_code == 200
    detail = editor_client.get(f"/lamulana/api/threads/{tid}").get_json()["thread"]
    assert detail["clues"] == []


def test_link_to_a_missing_thread_is_rejected(editor_client):
    cid, _ = _clue_and_thread(editor_client)
    r = editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": 9999})
    assert r.status_code == 404


def test_solving_marks_linked_clues_used(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    r = editor_client.post(f"/lamulana/api/threads/{tid}/solve", json={
        "solution": "incant Sol in front of the tablet", "mark_clues_used": True})
    assert r.status_code == 200
    data = r.get_json()
    assert data["thread"]["state"] == "solved"
    assert data["thread"]["solution"] == "incant Sol in front of the tablet"
    assert data["thread"]["solved_at"] > 0
    assert data["clues_marked"] == 1
    clue = editor_client.get("/lamulana/api/clues").get_json()["clues"][0]
    assert clue["state"] == "used"


def test_solving_can_leave_clues_alone(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    r = editor_client.post(f"/lamulana/api/threads/{tid}/solve", json={
        "solution": "x", "mark_clues_used": False})
    assert r.get_json()["clues_marked"] == 0
    clue = editor_client.get("/lamulana/api/clues").get_json()["clues"][0]
    assert clue["state"] == "understood"


def test_solving_defaults_to_marking_clues_used(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    r = editor_client.post(f"/lamulana/api/threads/{tid}/solve", json={"solution": "x"})
    assert r.get_json()["clues_marked"] == 1


def test_solving_does_not_touch_already_used_clues(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.patch(f"/lamulana/api/clues/{cid}", json={"state": "used"})
    editor_client.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    r = editor_client.post(f"/lamulana/api/threads/{tid}/solve", json={"solution": "x"})
    assert r.get_json()["clues_marked"] == 0


def test_link_and_solve_need_an_editing_session(reader_client):
    assert reader_client.post("/lamulana/api/link", json={}).status_code == 403
    assert reader_client.delete("/lamulana/api/link", json={}).status_code == 403
    assert reader_client.post("/lamulana/api/threads/1/solve", json={}).status_code == 403
