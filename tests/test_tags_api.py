"""Tag store + /api/tags, and the rename/delete integration points.

Tags are keyed by filename, like _LIBRARY and favorites.json. That means a
rename has to repoint them and a delete has to drop them, or the board shows
counts for clips that no longer exist.
"""


def set_tags(app, tags, assign):
    with app._TAGS_LOCK:
        app._TAGS["tags"] = tags
        app._TAGS["assign"] = assign
        app.save_tags()


def test_empty_store_returns_no_tags(client):
    d = client.get("/api/tags").get_json()
    assert d["tags"] == []


def test_tag_counts_reflect_assignments(app, client):
    set_tags(app, {"t": {"label": "T"}}, {"short_sound.wav": ["t"]})
    d = client.get("/api/tags").get_json()
    assert [(t["slug"], t["count"]) for t in d["tags"]] == [("t", 1)]


def test_counts_exclude_files_no_longer_in_the_library(app, client):
    # A ghost assignment must not inflate the count on the board.
    set_tags(app, {"t": {"label": "T"}},
             {"short_sound.wav": ["t"], "deleted.wav": ["t"]})
    d = client.get("/api/tags").get_json()
    assert d["tags"][0]["count"] == 1


def test_assign_map_is_filtered_to_live_files_too(app, client):
    set_tags(app, {"t": {"label": "T"}},
             {"short_sound.wav": ["t"], "deleted.wav": ["t"]})
    d = client.get("/api/tags").get_json()
    assert "deleted.wav" not in d["assign"]


def test_parent_count_rolls_up_its_children(app, client):
    set_tags(app,
             {"p": {"label": "P"}, "c": {"label": "C", "parent": "p"}},
             {"short_sound.wav": ["c"], "long_song.wav": ["p"]})
    by = {t["slug"]: t for t in client.get("/api/tags").get_json()["tags"]}
    assert by["c"]["count"] == 1
    assert by["p"]["count"] == 2       # its own clip plus the child's


def test_child_clip_is_not_double_counted_on_the_parent(app, client):
    set_tags(app,
             {"p": {"label": "P"}, "c": {"label": "C", "parent": "p"}},
             {"short_sound.wav": ["c", "p"]})
    by = {t["slug"]: t for t in client.get("/api/tags").get_json()["tags"]}
    assert by["p"]["count"] == 1


def test_song_count_is_reported_per_tag(app, client):
    set_tags(app, {"t": {"label": "T"}},
             {"short_sound.wav": ["t"], "long_song.wav": ["t"]})
    t = client.get("/api/tags").get_json()["tags"][0]
    assert t["songs"] == 1             # long_song.wav is >15s


def test_children_are_reported_with_their_parent(app, client):
    set_tags(app,
             {"p": {"label": "P"}, "c": {"label": "C", "parent": "p"}},
             {"short_sound.wav": ["c"]})
    by = {t["slug"]: t for t in client.get("/api/tags").get_json()["tags"]}
    assert by["c"]["parent"] == "p"
    assert by["p"].get("parent") is None


def test_reading_tags_needs_no_login(client):
    assert client.get("/api/tags").status_code == 200


def test_rename_repoints_assignments(app, editor_client):
    set_tags(app, {"t": {"label": "T"}}, {"short_sound.wav": ["t"]})
    r = editor_client.post("/api/rename",
                           json={"file": "short_sound.wav", "name": "renamed"})
    assert r.get_json()["ok"] is True
    assert app._TAGS["assign"].get("renamed.wav") == ["t"]
    assert "short_sound.wav" not in app._TAGS["assign"]
    # and the count survives the rename
    assert editor_client.get("/api/tags").get_json()["tags"][0]["count"] == 1


def test_rename_of_an_untagged_clip_does_not_crash(app, editor_client):
    set_tags(app, {"t": {"label": "T"}}, {})
    r = editor_client.post("/api/rename",
                           json={"file": "short_sound.wav", "name": "renamed"})
    assert r.get_json()["ok"] is True


def test_delete_drops_assignments(app, client):
    set_tags(app, {"t": {"label": "T"}}, {"short_sound.wav": ["t"]})
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["admin"] = True
    assert c.post("/api/delete", json={"file": "short_sound.wav"}).get_json()["ok"] is True
    assert "short_sound.wav" not in app._TAGS["assign"]


def test_store_survives_a_reload_from_disk(app):
    set_tags(app, {"t": {"label": "T"}}, {"short_sound.wav": ["t"]})
    reloaded = app._load(app.TAGS_FILE, {"tags": {}, "assign": {}})
    assert reloaded["tags"]["t"]["label"] == "T"
    assert reloaded["assign"]["short_sound.wav"] == ["t"]


def test_retired_list_survives_a_save(app):
    # seed_tags.py --merge reads "retired" to keep deliberately deleted tags
    # dead. If the server drops the key on its next write, deleted tags come
    # back the next time anyone runs a merge.
    with app._TAGS_LOCK:
        app._TAGS["retired"] = ["sfx"]
        app.save_tags()
    assert app._load(app.TAGS_FILE, {}).get("retired") == ["sfx"]


def test_retired_list_is_loaded_from_disk(app, tmp_path):
    import json
    json.dump({"tags": {"x": {"label": "X"}}, "assign": {}, "retired": ["sfx", "ts"]},
              open(app.TAGS_FILE, "w"))
    got = app._norm_tags(app._load(app.TAGS_FILE, {}))
    assert got["retired"] == ["sfx", "ts"]


def test_retired_is_absent_when_the_store_has_none(app):
    got = app._norm_tags({"tags": {}, "assign": {}})
    assert "retired" not in got
