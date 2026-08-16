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
    assert counts == {
        "clue_state": {"raw": 1, "understood": 1, "used": 0},
        "thread_state": {"open": 1, "solved": 1},
        "clue_area": {},
        "thread_area": {},
    }


def test_bootstrap_area_counts_distinguish_clues_from_threads(editor_client):
    """A shared area holding both a clue and a thread must not let one kind's
    count leak into the other's -- the failure mode a single UNION ALL query
    with a dropped `kind` discriminator would produce.
    """
    ann = _area_id(editor_client, "Annwfn")
    val = _area_id(editor_client, "Valhalla")
    editor_client.post("/lamulana/api/clues", json={"title": "a", "area_id": ann})
    editor_client.post("/lamulana/api/clues", json={"title": "b", "area_id": ann})
    editor_client.post("/lamulana/api/threads", json={"title": "t", "area_id": ann})
    editor_client.post("/lamulana/api/threads", json={"title": "u", "area_id": val})
    counts = editor_client.get("/lamulana/api/bootstrap").get_json()["counts"]
    # JSON object keys are always strings; area ids round-trip as str(id).
    assert counts["clue_area"] == {str(ann): 2}
    assert counts["thread_area"] == {str(ann): 1, str(val): 1}


def test_bootstrap_state_counts_update_after_a_state_change(editor_client):
    """Counts must reflect current rows, not a snapshot from create time."""
    cid = editor_client.post("/lamulana/api/clues", json={"title": "a"}
                              ).get_json()["clue"]["id"]
    tid = editor_client.post("/lamulana/api/threads", json={"title": "t"}
                              ).get_json()["thread"]["id"]
    before = editor_client.get("/lamulana/api/bootstrap").get_json()["counts"]
    assert before["clue_state"] == {"raw": 1, "understood": 0, "used": 0}
    assert before["thread_state"] == {"open": 1, "solved": 0}

    editor_client.patch(f"/lamulana/api/clues/{cid}", json={"state": "understood"})
    editor_client.patch(f"/lamulana/api/threads/{tid}", json={"state": "solved"})
    after = editor_client.get("/lamulana/api/bootstrap").get_json()["counts"]
    assert after["clue_state"] == {"raw": 0, "understood": 1, "used": 0}
    assert after["thread_state"] == {"open": 0, "solved": 1}


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


def test_clues_filter_by_source(editor_client):
    editor_client.post("/lamulana/api/clues", json={"title": "a", "source": "npc"})
    editor_client.post("/lamulana/api/clues", json={"title": "b", "source": "tablet"})
    got = editor_client.get("/lamulana/api/clues",
                             query_string={"source": "npc"}).get_json()["clues"]
    assert [c["title"] for c in got] == ["a"]


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

    The clue_thread rows are inserted directly rather than via POST /api/link,
    the way test_bootstrap_counts_distinguish_their_sources inserts straight
    into clue/thread above, so this test stays independent of the link route
    and exercises only the detail view's own JOIN. This is what proves the
    appended JOIN in
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


def _clue(client, title, state="understood"):
    return client.post("/lamulana/api/clues", json={
        "title": title, "body": title, "state": state}).get_json()["clue"]["id"]


def _thread(client, title):
    return client.post("/lamulana/api/threads", json={"title": title}
                       ).get_json()["thread"]["id"]


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


# --- Multi-entity fixtures: a single clue/thread pair cannot distinguish
# "clues linked to this thread" from "all clues", so the cascade's scoping
# (the property the whole feature rests on) needs a population bigger than one
# to actually exercise it. -------------------------------------------------

def test_solving_marks_only_that_threads_clues(editor_client):
    c = editor_client
    shared = _clue(c, "shared")
    other = _clue(c, "other")
    loose = _clue(c, "loose")
    t1, t2 = _thread(c, "t1"), _thread(c, "t2")
    for cid, tid in ((shared, t1), (shared, t2), (other, t2)):
        c.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": tid})
    r = c.post(f"/lamulana/api/threads/{t1}/solve", json={"solution": "x"})
    assert r.get_json()["clues_marked"] == 1
    st = {x["id"]: x["state"] for x in c.get("/lamulana/api/clues").get_json()["clues"]}
    assert st[shared] == "used"
    assert st[other] == "understood"
    assert st[loose] == "understood"


def test_unlink_from_one_thread_leaves_the_other_link_intact(editor_client):
    c = editor_client
    cid = _clue(c, "shared")
    t1, t2 = _thread(c, "t1"), _thread(c, "t2")
    c.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": t1})
    c.post("/lamulana/api/link", json={"clue_id": cid, "thread_id": t2})
    assert c.delete("/lamulana/api/link",
                     json={"clue_id": cid, "thread_id": t1}).status_code == 200
    detail1 = c.get(f"/lamulana/api/threads/{t1}").get_json()["thread"]
    detail2 = c.get(f"/lamulana/api/threads/{t2}").get_json()["thread"]
    assert detail1["clues"] == []
    assert [cl["id"] for cl in detail2["clues"]] == [cid]


# --- _link_pair must validate types the way _clean_body does elsewhere,
# not bind raw JSON straight into SQLite. ------------------------------------

def test_link_rejects_a_list_body_400s_not_500s(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    r = editor_client.post("/lamulana/api/link", json={"clue_id": [cid], "thread_id": tid})
    assert r.status_code == 400
    r2 = editor_client.delete("/lamulana/api/link", json={"clue_id": {"a": 1}, "thread_id": tid})
    assert r2.status_code == 400


def test_link_rejects_a_boolean_id_instead_of_treating_it_as_1(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    r = editor_client.post("/lamulana/api/link", json={"clue_id": True, "thread_id": True})
    assert r.status_code == 400
    # And it must not have linked clue id 1 to thread id 1 as a side effect.
    detail = editor_client.get(f"/lamulana/api/threads/{tid}").get_json()["thread"]
    assert detail["clues"] == []


def test_link_id_zero_is_a_404_not_a_400(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    r = editor_client.post("/lamulana/api/link", json={"clue_id": 0, "thread_id": tid})
    assert r.status_code == 404


# --- Solve must not clobber a solution already saved on the thread, and must
# agree with PATCH about when solved_at is allowed to change. ---------------

def test_solve_without_a_solution_preserves_one_already_saved(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.patch(f"/lamulana/api/threads/{tid}",
                         json={"solution": "typed into the edit form earlier"})
    r = editor_client.post(f"/lamulana/api/threads/{tid}/solve", json={})
    assert r.get_json()["thread"]["solution"] == "typed into the edit form earlier"


def test_solve_with_explicit_null_solution_clears_it(editor_client):
    cid, tid = _clue_and_thread(editor_client)
    editor_client.patch(f"/lamulana/api/threads/{tid}", json={"solution": "old"})
    r = editor_client.post(f"/lamulana/api/threads/{tid}/solve", json={"solution": None})
    assert r.get_json()["thread"]["solution"] is None


def test_resolving_does_not_move_solved_at(editor_client, monkeypatch):
    # Same reasoning as test_solved_at_bookkeeping above: both solve calls
    # below land in the same wall-clock second in practice, which would let a
    # dropped "AND solved_at IS NULL" guard pass by accident. Fake _now() to
    # hand out a fresh value each call so "unchanged" is a real assertion
    # about which branch ran, not an artifact of clock resolution.
    api = sys.modules["lamulana.api"]
    counter = itertools.count(1000, 1000)
    monkeypatch.setattr(api, "_now", lambda: next(counter))

    cid, tid = _clue_and_thread(editor_client)
    first = editor_client.post(f"/lamulana/api/threads/{tid}/solve",
                                json={"solution": "a"}).get_json()["thread"]["solved_at"]
    second = editor_client.post(f"/lamulana/api/threads/{tid}/solve",
                                 json={"solution": "b"}).get_json()["thread"]["solved_at"]
    assert second == first


def test_solving_a_missing_thread_404s(editor_client):
    r = editor_client.post("/lamulana/api/threads/9999/solve", json={"solution": "x"})
    assert r.status_code == 404


def test_search_spans_clues_and_threads(editor_client):
    editor_client.post("/lamulana/api/clues", json={
        "title": "serpent tablet", "body": "where the twin serpents meet"})
    editor_client.post("/lamulana/api/threads", json={
        "title": "serpent door", "body": "won't open"})
    data = editor_client.get("/lamulana/api/search?q=serpent").get_json()
    assert len(data["clues"]) == 1
    assert len(data["threads"]) == 1


def test_search_ands_every_word(editor_client):
    editor_client.post("/lamulana/api/clues", json={
        "title": "a", "body": "where the twin serpents meet"})
    editor_client.post("/lamulana/api/clues", json={"title": "b", "body": "twin peaks"})
    hits = editor_client.get("/lamulana/api/search?q=twin serpents").get_json()["clues"]
    assert [c["title"] for c in hits] == ["a"]


def test_search_matches_interpretation_too(editor_client):
    editor_client.post("/lamulana/api/clues", json={
        "title": "a", "interpretation": "this is about the Valhalla ankh"})
    hits = editor_client.get("/lamulana/api/search?q=valhalla").get_json()["clues"]
    assert len(hits) == 1


def test_empty_search_returns_nothing(editor_client):
    editor_client.post("/lamulana/api/clues", json={"title": "a"})
    data = editor_client.get("/lamulana/api/search?q=").get_json()
    assert data == {"clues": [], "threads": []}


def test_search_orders_same_second_clues_newest_id_first(editor_client, monkeypatch):
    # Same reasoning as test_resolving_does_not_move_solved_at above: pin
    # _now() so both clues land in the same second, the case that would let a
    # missing `c.id DESC` tiebreak sort them differently than api_clues does.
    api = sys.modules["lamulana.api"]
    monkeypatch.setattr(api, "_now", lambda: 1000)
    editor_client.post("/lamulana/api/clues", json={"title": "a", "body": "tiebreakword"})
    editor_client.post("/lamulana/api/clues", json={"title": "b", "body": "tiebreakword"})
    hits = editor_client.get("/lamulana/api/search?q=tiebreakword").get_json()["clues"]
    assert [c["title"] for c in hits] == ["b", "a"]


def test_search_orders_same_second_threads_newest_id_first(editor_client, monkeypatch):
    api = sys.modules["lamulana.api"]
    monkeypatch.setattr(api, "_now", lambda: 1000)
    editor_client.post("/lamulana/api/threads", json={"title": "a", "body": "tiebreakword"})
    editor_client.post("/lamulana/api/threads", json={"title": "b", "body": "tiebreakword"})
    hits = editor_client.get("/lamulana/api/search?q=tiebreakword").get_json()["threads"]
    assert [t["title"] for t in hits] == ["b", "a"]


def _flatten_checklist(groups):
    """{item_id: item} across every group -- enough rows that a write path
    scoped only by a dropped WHERE clause (touching every row, not just the
    one it was pointed at) shows up as more than one changed id."""
    return {i["id"]: i for g in groups for i in g["items"]}


def _get_checklist(client):
    return _flatten_checklist(client.get("/lamulana/api/checklist").get_json()["groups"])


def test_checklist_toggle_stamps_and_clears_done_at(editor_client):
    before = _get_checklist(editor_client)
    target_id = next(iter(before))
    r = editor_client.patch(f"/lamulana/api/checklist/{target_id}", json={"done": True})
    assert r.get_json()["item"]["done"] is True
    assert r.get_json()["item"]["done_at"] > 0
    after = _get_checklist(editor_client)
    # Every other row -- not just index 0 -- must be untouched by a WHERE
    # clause that only looks correct against a single-row fixture.
    assert {i for i in before if before[i] != after[i]} == {target_id}

    r = editor_client.patch(f"/lamulana/api/checklist/{target_id}", json={"done": False})
    assert r.get_json()["item"]["done"] is False
    assert r.get_json()["item"]["done_at"] is None
    final = _get_checklist(editor_client)
    assert {i for i in after if after[i] != final[i]} == {target_id}


def test_checklist_note(editor_client):
    before = _get_checklist(editor_client)
    target_id = next(iter(before))
    r = editor_client.patch(f"/lamulana/api/checklist/{target_id}",
                             json={"note": "behind the ice"})
    assert r.get_json()["item"]["note"] == "behind the ice"
    after = _get_checklist(editor_client)
    assert {i for i in before if before[i] != after[i]} == {target_id}


def test_checklist_patch_missing_id_404s(editor_client):
    r = editor_client.patch("/lamulana/api/checklist/999999", json={"done": True})
    assert r.status_code == 404


def test_checklist_done_must_be_a_boolean(editor_client):
    before = _get_checklist(editor_client)
    target_id = next(iter(before))
    for bad in ("false", 1, None, [True]):
        r = editor_client.patch(f"/lamulana/api/checklist/{target_id}", json={"done": bad})
        assert r.status_code == 400
    assert _get_checklist(editor_client) == before


def test_checklist_patch_with_nothing_to_change_400s(editor_client):
    before = _get_checklist(editor_client)
    target_id = next(iter(before))
    # An empty body, and a body carrying only fields PATCH doesn't recognize
    # (group/name -- POST-only, there is no rename), both count as nothing.
    for body in ({}, {"name": "Renamed"}, {"group": "Elsewhere"}):
        r = editor_client.patch(f"/lamulana/api/checklist/{target_id}", json=body)
        assert r.status_code == 400
    assert _get_checklist(editor_client) == before


def test_add_and_remove_a_custom_row(editor_client):
    r = editor_client.post("/lamulana/api/checklist",
                            json={"group": "Guardians", "name": "my own note"})
    assert r.status_code == 200
    new_id = r.get_json()["item"]["id"]
    groups = {g["group"]: g for g in
              editor_client.get("/lamulana/api/checklist").get_json()["groups"]}
    assert len(groups["Guardians"]["items"]) == 11
    assert editor_client.delete(f"/lamulana/api/checklist/{new_id}").status_code == 200
    groups = {g["group"]: g for g in
              editor_client.get("/lamulana/api/checklist").get_json()["groups"]}
    assert len(groups["Guardians"]["items"]) == 10


def test_a_custom_row_can_open_a_new_group(editor_client):
    editor_client.post("/lamulana/api/checklist",
                        json={"group": "Garbs", "name": "Clay Doll Suit"})
    groups = {g["group"]: g for g in
              editor_client.get("/lamulana/api/checklist").get_json()["groups"]}
    assert [i["name"] for i in groups["Garbs"]["items"]] == ["Clay Doll Suit"]


def test_a_new_group_sorts_after_every_seeded_group(editor_client):
    # Pins _GROUP_RANK's fallback rank (len(_GROUP_RANK), not e.g. -1): an
    # unseeded group belongs at the end of the progression order, not ahead
    # of Guardians -- the seed's authored order is what the Progress screen
    # is meant to render in.
    editor_client.post("/lamulana/api/checklist", json={"group": "Zzz Custom", "name": "x"})
    names = [g["group"] for g in
             editor_client.get("/lamulana/api/checklist").get_json()["groups"]]
    assert names == ["Guardians", "Sacred Orbs", "Mantras", "Maps", "Apps", "Zzz Custom"]


def test_duplicate_custom_row_is_rejected(editor_client):
    editor_client.post("/lamulana/api/checklist", json={"group": "Garbs", "name": "x"})
    r = editor_client.post("/lamulana/api/checklist", json={"group": "Garbs", "name": "X"})
    assert r.status_code == 409


def test_duplicate_custom_row_leaves_no_open_transaction(editor_client):
    """A UNIQUE violation aborts the INSERT statement, not the transaction
    sqlite3's implicit BEGIN opened for it. Without an explicit rollback in
    the 409 branch, this thread's connection is left holding the WAL write
    lock -- under waitress, where each request thread owns its own
    connection, that poisons every later write on this same thread, not just
    the one that 409'd.
    """
    api = sys.modules["lamulana.api"]
    editor_client.post("/lamulana/api/checklist", json={"group": "Garbs", "name": "x"})
    r = editor_client.post("/lamulana/api/checklist", json={"group": "Garbs", "name": "X"})
    assert r.status_code == 409
    assert api._conn().in_transaction is False
    # And the connection still works for a normal write afterward.
    r = editor_client.post("/lamulana/api/checklist", json={"group": "Garbs", "name": "y"})
    assert r.status_code == 200


def test_checklist_add_rejects_whitespace_only_group(editor_client):
    before = _get_checklist(editor_client)
    r = editor_client.post("/lamulana/api/checklist", json={"group": "   ", "name": "x"})
    assert r.status_code == 400
    assert _get_checklist(editor_client) == before


# --- Bad input must 400, never reach SQLite as a 500 (same gap Task 4 found
# and fixed for clues -- see _clean_body) ------------------------------------

def test_checklist_add_rejects_a_non_string_group(editor_client):
    before = editor_client.get("/lamulana/api/checklist").get_json()["groups"]
    r = editor_client.post("/lamulana/api/checklist", json={"group": 123, "name": "x"})
    assert r.status_code == 400
    assert editor_client.get("/lamulana/api/checklist").get_json()["groups"] == before


def test_checklist_add_rejects_a_non_string_name(editor_client):
    before = editor_client.get("/lamulana/api/checklist").get_json()["groups"]
    r = editor_client.post("/lamulana/api/checklist", json={"group": "Garbs", "name": 123})
    assert r.status_code == 400
    assert editor_client.get("/lamulana/api/checklist").get_json()["groups"] == before


def test_checklist_note_rejects_a_non_string(editor_client):
    item = editor_client.get("/lamulana/api/checklist").get_json()["groups"][0]["items"][0]
    r = editor_client.patch(f"/lamulana/api/checklist/{item['id']}", json={"note": 123})
    assert r.status_code == 400
    got = editor_client.get("/lamulana/api/checklist").get_json()["groups"][0]["items"][0]
    assert got["note"] == item["note"]


def test_checklist_writes_need_an_editing_session(reader_client):
    assert reader_client.patch("/lamulana/api/checklist/1", json={}).status_code == 403
    assert reader_client.post("/lamulana/api/checklist", json={}).status_code == 403
    assert reader_client.delete("/lamulana/api/checklist/1").status_code == 403
