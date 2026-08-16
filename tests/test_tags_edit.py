"""Tag editing: create, rename, re-parent, merge, delete, and per-clip tagging.

Every write is gated the same way Add/Edit is (can_edit). Reading and
filtering stay open, because filtering is playing.
"""


def set_tags(app, tags, assign):
    with app._TAGS_LOCK:
        app._TAGS["tags"] = tags
        app._TAGS["assign"] = assign
        app.save_tags()


def slugs(client):
    return {t["slug"]: t for t in client.get("/api/tags").get_json()["tags"]}


# ---- auth ----------------------------------------------------------------

def test_creating_a_tag_needs_edit_rights(client):
    r = client.post("/api/tags", json={"action": "create", "label": "New"})
    assert r.status_code == 403


def test_assigning_needs_edit_rights(client):
    r = client.post("/api/tags/assign",
                    json={"file": "short_sound.wav", "tags": []})
    assert r.status_code == 403


# ---- create --------------------------------------------------------------

def test_create_makes_a_slug_from_the_label(editor_client):
    r = editor_client.post("/api/tags", json={"action": "create", "label": "Hollow Knight"})
    assert r.get_json()["ok"] is True
    assert "hollow-knight" in slugs(editor_client)


def test_create_rejects_an_empty_label(editor_client):
    r = editor_client.post("/api/tags", json={"action": "create", "label": "  "})
    assert r.status_code == 400


def test_create_refuses_a_duplicate_slug(app, editor_client):
    set_tags(app, {"x": {"label": "X"}}, {})
    r = editor_client.post("/api/tags", json={"action": "create", "label": "X"})
    assert r.status_code == 409


# ---- rename --------------------------------------------------------------

def test_rename_changes_the_label_only(app, editor_client):
    set_tags(app, {"mew": {"label": "mewithoutYou"}}, {"short_sound.wav": ["mew"]})
    r = editor_client.post("/api/tags",
                           json={"action": "rename", "slug": "mew", "label": "Mewgenics"})
    assert r.get_json()["ok"] is True
    t = slugs(editor_client)["mew"]
    assert t["label"] == "Mewgenics"
    assert t["count"] == 1          # assignments untouched


def test_rename_of_an_unknown_tag_404s(editor_client):
    r = editor_client.post("/api/tags",
                           json={"action": "rename", "slug": "nope", "label": "X"})
    assert r.status_code == 404


# ---- re-parent -----------------------------------------------------------

def test_reparent_nests_a_tag(app, editor_client):
    set_tags(app, {"p": {"label": "P"}, "c": {"label": "C"}}, {"short_sound.wav": ["c"]})
    assert editor_client.post("/api/tags",
                              json={"action": "reparent", "slug": "c", "parent": "p"}
                              ).get_json()["ok"] is True
    s = slugs(editor_client)
    assert s["c"]["parent"] == "p"
    assert s["p"]["count"] == 1     # rolls up without rewriting assignments


def test_reparent_to_null_promotes_to_top_level(app, editor_client):
    set_tags(app, {"p": {"label": "P"}, "c": {"label": "C", "parent": "p"}}, {})
    editor_client.post("/api/tags", json={"action": "reparent", "slug": "c", "parent": None})
    assert slugs(editor_client)["c"].get("parent") is None


def test_a_tag_cannot_parent_itself(app, editor_client):
    set_tags(app, {"c": {"label": "C"}}, {})
    r = editor_client.post("/api/tags", json={"action": "reparent", "slug": "c", "parent": "c"})
    assert r.status_code == 400


def test_nesting_stays_one_level_deep(app, editor_client):
    # p > c already; making g a child of c would be two levels.
    set_tags(app, {"p": {"label": "P"}, "c": {"label": "C", "parent": "p"},
                   "g": {"label": "G"}}, {})
    r = editor_client.post("/api/tags", json={"action": "reparent", "slug": "g", "parent": "c"})
    assert r.status_code == 400


def test_a_tag_with_children_cannot_become_a_child(app, editor_client):
    set_tags(app, {"p": {"label": "P"}, "c": {"label": "C", "parent": "p"},
                   "o": {"label": "O"}}, {})
    r = editor_client.post("/api/tags", json={"action": "reparent", "slug": "p", "parent": "o"})
    assert r.status_code == 400


# ---- merge ---------------------------------------------------------------

def test_merge_moves_assignments_and_drops_the_source(app, editor_client):
    set_tags(app, {"hm": {"label": "HM"}, "hm2": {"label": "HM2"}},
             {"short_sound.wav": ["hm"], "long_song.wav": ["hm2"]})
    r = editor_client.post("/api/tags", json={"action": "merge", "slug": "hm2", "into": "hm"})
    assert r.get_json()["ok"] is True
    s = slugs(editor_client)
    assert "hm2" not in s
    assert s["hm"]["count"] == 2


def test_merge_does_not_duplicate_a_clip_tagged_with_both(app, editor_client):
    set_tags(app, {"a": {"label": "A"}, "b": {"label": "B"}},
             {"short_sound.wav": ["a", "b"]})
    editor_client.post("/api/tags", json={"action": "merge", "slug": "b", "into": "a"})
    assert app._TAGS["assign"]["short_sound.wav"] == ["a"]


def test_merge_into_itself_is_rejected(app, editor_client):
    set_tags(app, {"a": {"label": "A"}}, {})
    r = editor_client.post("/api/tags", json={"action": "merge", "slug": "a", "into": "a"})
    assert r.status_code == 400


def test_merging_a_parent_moves_its_children(app, editor_client):
    set_tags(app, {"p": {"label": "P"}, "k": {"label": "K", "parent": "p"},
                   "q": {"label": "Q"}}, {"short_sound.wav": ["k"]})
    editor_client.post("/api/tags", json={"action": "merge", "slug": "p", "into": "q"})
    s = slugs(editor_client)
    assert "p" not in s
    assert s["k"]["parent"] == "q"
    assert s["q"]["count"] == 1


# ---- delete --------------------------------------------------------------

def test_delete_removes_the_tag_and_its_assignments(app, editor_client):
    set_tags(app, {"x": {"label": "X"}}, {"short_sound.wav": ["x"]})
    assert editor_client.post("/api/tags",
                              json={"action": "delete", "slug": "x"}).get_json()["ok"] is True
    assert "x" not in slugs(editor_client)
    assert "short_sound.wav" not in app._TAGS["assign"]


def test_deleting_a_parent_promotes_its_children(app, editor_client):
    set_tags(app, {"p": {"label": "P"}, "c": {"label": "C", "parent": "p"}},
             {"short_sound.wav": ["c"]})
    editor_client.post("/api/tags", json={"action": "delete", "slug": "p"})
    s = slugs(editor_client)
    assert s["c"].get("parent") is None
    assert s["c"]["count"] == 1     # the child's own clips survive


# ---- per-clip assignment -------------------------------------------------

def test_assign_replaces_a_clips_tags(app, editor_client):
    set_tags(app, {"a": {"label": "A"}, "b": {"label": "B"}}, {"short_sound.wav": ["a"]})
    r = editor_client.post("/api/tags/assign",
                           json={"file": "short_sound.wav", "tags": ["b"]})
    assert r.get_json()["ok"] is True
    assert app._TAGS["assign"]["short_sound.wav"] == ["b"]


def test_assigning_an_empty_list_clears_the_clip(app, editor_client):
    set_tags(app, {"a": {"label": "A"}}, {"short_sound.wav": ["a"]})
    editor_client.post("/api/tags/assign", json={"file": "short_sound.wav", "tags": []})
    assert "short_sound.wav" not in app._TAGS["assign"]


def test_assigning_an_unknown_tag_is_rejected(app, editor_client):
    set_tags(app, {"a": {"label": "A"}}, {})
    r = editor_client.post("/api/tags/assign",
                           json={"file": "short_sound.wav", "tags": ["nope"]})
    assert r.status_code == 400


def test_assigning_to_a_file_not_in_the_library_404s(app, editor_client):
    set_tags(app, {"a": {"label": "A"}}, {})
    r = editor_client.post("/api/tags/assign",
                           json={"file": "ghost.wav", "tags": ["a"]})
    assert r.status_code == 404


def test_assignment_survives_a_reload_from_disk(app, editor_client):
    set_tags(app, {"a": {"label": "A"}}, {})
    editor_client.post("/api/tags/assign",
                       json={"file": "short_sound.wav", "tags": ["a"]})
    on_disk = app._load(app.TAGS_FILE, {})
    assert on_disk["assign"]["short_sound.wav"] == ["a"]
