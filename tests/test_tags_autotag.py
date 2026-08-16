"""Auto-tagging new clips, and the on-disk history snapshots.

Without this, every clip added by upload or URL fetch lands untagged forever
and the tagged share of the library decays as the library grows.

The prefix index is derived from what is actually assigned to each tag, not
from slugs — slugs stopped matching prefixes once tags got real names
("Bob's Burgers" -> bob-s-burgers, whose files are bb_*).
"""
import os


def set_tags(app, tags, assign):
    with app._TAGS_LOCK:
        app._TAGS["tags"] = tags
        app._TAGS["assign"] = assign
        app.save_tags()


def make_clip(app, name):
    """Drop a silent wav into the library and rescan."""
    import struct
    import wave
    p = os.path.join(app.SOUND_DIR, name)
    with wave.open(p, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(struct.pack("<" + "h" * 8000, *([0] * 8000)))
    app.scan_library()
    return name


# ---- prefix index ---------------------------------------------------------

def test_index_maps_a_prefix_to_its_dominant_tag(app):
    set_tags(app, {"x": {"label": "X"}},
             {"foo_a.wav": ["x"], "foo_b.wav": ["x"], "foo_c.wav": ["x"]})
    assert app.tag_prefix_index().get("foo") == "x"


def test_index_ignores_a_prefix_with_too_little_support(app):
    set_tags(app, {"x": {"label": "X"}}, {"foo_a.wav": ["x"], "foo_b.wav": ["x"]})
    assert "foo" not in app.tag_prefix_index()


def test_index_ignores_an_ambiguous_prefix(app):
    # the_ appears under two unrelated tags — guessing would be wrong
    set_tags(app, {"a": {"label": "A"}, "b": {"label": "B"}},
             {"the_1.wav": ["a"], "the_2.wav": ["a"], "the_3.wav": ["b"], "the_4.wav": ["b"]})
    assert "the" not in app.tag_prefix_index()


def test_index_works_when_the_slug_is_nothing_like_the_prefix(app):
    # the real case: files are bb_*, the tag slug is bob-s-burgers
    set_tags(app, {"bob-s-burgers": {"label": "Bob's Burgers"}},
             {"bb_1.wav": ["bob-s-burgers"], "bb_2.wav": ["bob-s-burgers"],
              "bb_3.wav": ["bob-s-burgers"]})
    assert app.tag_prefix_index().get("bb") == "bob-s-burgers"


def test_index_skips_names_with_no_underscore(app):
    set_tags(app, {"x": {"label": "X"}},
             {"aa.wav": ["x"], "bb.wav": ["x"], "cc.wav": ["x"]})
    assert app.tag_prefix_index() == {}


# ---- autotag --------------------------------------------------------------

def test_a_new_clip_matching_a_known_prefix_is_tagged(app):
    set_tags(app, {"x": {"label": "X"}},
             {"foo_a.wav": ["x"], "foo_b.wav": ["x"], "foo_c.wav": ["x"]})
    fn = make_clip(app, "foo_brand_new.wav")
    assert app.tags_autotag(fn) == ["x"]
    assert app._TAGS["assign"][fn] == ["x"]


def test_an_unrecognised_prefix_is_left_alone(app):
    set_tags(app, {"x": {"label": "X"}},
             {"foo_a.wav": ["x"], "foo_b.wav": ["x"], "foo_c.wav": ["x"]})
    fn = make_clip(app, "zzz_unknown.wav")
    assert app.tags_autotag(fn) == []
    assert fn not in app._TAGS["assign"]


def test_autotag_never_overwrites_an_existing_assignment(app):
    set_tags(app, {"x": {"label": "X"}, "y": {"label": "Y"}},
             {"foo_a.wav": ["x"], "foo_b.wav": ["x"], "foo_c.wav": ["x"],
              "foo_keep.wav": ["y"]})
    assert app.tags_autotag("foo_keep.wav") == []
    assert app._TAGS["assign"]["foo_keep.wav"] == ["y"]


def test_upload_autotags(app, editor_client):
    import io
    set_tags(app, {"x": {"label": "X"}},
             {"foo_a.wav": ["x"], "foo_b.wav": ["x"], "foo_c.wav": ["x"]})
    data = {"file": (io.BytesIO(b"RIFF0000WAVEfmt "), "foo_uploaded.wav")}
    r = editor_client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert r.get_json()["ok"] is True
    assert app._TAGS["assign"].get("foo_uploaded.wav") == ["x"]


# ---- history snapshots ----------------------------------------------------

def test_first_save_writes_a_history_snapshot(app):
    set_tags(app, {"x": {"label": "X"}}, {})
    hist = os.path.join(app.DATA_DIR, "tags-history")
    assert os.path.isdir(hist) and os.listdir(hist)


def test_snapshots_are_throttled_not_one_per_write(app):
    hist = os.path.join(app.DATA_DIR, "tags-history")
    for i in range(5):
        set_tags(app, {"x": {"label": "X%d" % i}}, {})
    assert len(os.listdir(hist)) == 1


def test_history_is_capped(app):
    hist = os.path.join(app.DATA_DIR, "tags-history")
    os.makedirs(hist, exist_ok=True)
    for i in range(40):
        open(os.path.join(hist, "tags-20260101-0000%02d.json" % i), "w").write("{}")
    set_tags(app, {"x": {"label": "X"}}, {})
    app._prune_tag_history()
    assert len(os.listdir(hist)) <= app.TAG_HISTORY_KEEP
