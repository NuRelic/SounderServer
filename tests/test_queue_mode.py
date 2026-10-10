"""Queue mode (docs/queue-mode.md): songs line up instead of cutting each other off,
plus skip / prev / seek / reorder and play-an-album-from-a-track."""
import time
import pytest

from tests.conftest import _write_wav


@pytest.fixture
def clock(monkeypatch):
    holder = {"t": time.time()}          # real epoch: a 1970 clock would expire the session cookie
    monkeypatch.setattr(time, "time", lambda: holder["t"])
    return holder


@pytest.fixture
def album(app, tmp_path):
    """Five 'EPIC' songs (20s each) tagged e, out of order on disk, plus a straggler name."""
    sounds = tmp_path / "sounds"
    names = ["e10_storm.wav", "e2_just_a_man.wav", "e1_horse.wav", "e6_polyphemus.wav", "epic_14_puppeteer.wav"]
    for n in names:
        _write_wav(sounds / n, 20.0)
    app.scan_library()
    with app._TAGS_LOCK:
        app._TAGS["tags"]["e"] = {"label": "EPIC: The Musical"}
        for n in names:
            app._TAGS["assign"][n] = ["e"]
    return names


def _cur(app):
    return (app.queue_state()["current"] or {}).get("file")


def _finish_current(app, clock):
    e = next(a for a in app._ACTIVE if a.get("queue"))
    clock["t"] = e["start"] + e["dur"] + 5
    app.queue_tick()


def test_first_song_plays_now_second_waits(app, clock):
    app.queue_add(["long_song.wav", "long_song.wav"], "bob")
    st = app.queue_state()
    assert st["current"]["file"] == "long_song.wav" and len(st["items"]) == 1
    _finish_current(app, clock)
    st = app.queue_state()
    assert st["current"] and st["items"] == [] and st["history"] == 1


def test_skip_and_prev(app, clock, album):
    app.queue_add(["e1_horse.wav", "e2_just_a_man.wav", "e6_polyphemus.wav"], "bob")
    assert _cur(app) == "e1_horse.wav"
    assert app.queue_skip() and _cur(app) == "e2_just_a_man.wav"
    assert app.queue_prev() and _cur(app) == "e1_horse.wav"
    assert [i["file"] for i in app.queue_state()["items"]] == ["e2_just_a_man.wav", "e6_polyphemus.wav"]


def test_seek_moves_shared_start(app, clock):
    app.queue_add(["long_song.wav"], "bob")
    tok = app.queue_state()["current"]["token"]
    e = app.queue_seek(tok, 12.0)
    assert e["seek"] == 1
    assert clock["t"] - e["start"] - app.SYNC_BUFFER == pytest.approx(12.0)
    assert app.queue_state()["current"]["pos"] == pytest.approx(12.0)
    assert app.queue_seek(tok, 999)["start"] > clock["t"] - 20 - app.SYNC_BUFFER   # clamped


def test_move_remove_clear(app, clock, album):
    app.queue_add(["e1_horse.wav", "e2_just_a_man.wav", "e6_polyphemus.wav", "e10_storm.wav"], "bob")
    ids = [i["id"] for i in app.queue_state()["items"]]
    assert app.queue_move(ids[2], 0)
    assert [i["file"] for i in app.queue_state()["items"]][0] == "e10_storm.wav"
    assert app.queue_remove(ids[0])
    assert len(app.queue_state()["items"]) == 2
    app.queue_clear()
    assert app.queue_state()["items"] == [] and _cur(app) == "e1_horse.wav"   # clear keeps the playing song


def test_manual_song_in_overlap_mode_then_queue_resumes(app, clock, album):
    app.queue_add(["e1_horse.wav", "e2_just_a_man.wav"], "bob")
    clock["t"] += 2                                   # past the interrupt floor (MIN_VISIBLE)
    app.fire("e10_storm.wav", "amy", lane=0)          # overlap: a hand-fired song takes the song lane
    clock["t"] += 2
    app.queue_tick()
    assert _cur(app) is None and app.queue_state()["items"][0]["file"] == "e2_just_a_man.wav"
    manual = next(a for a in app._ACTIVE if a["file"] == "e10_storm.wav")
    clock["t"] = manual["start"] + manual["dur"] + 5
    app.queue_tick()
    assert _cur(app) == "e2_just_a_man.wav"


def test_queue_mode_click_enqueues_songs_but_fires_sounds(app, client, clock):
    app._QUEUE["mode"] = "queue"
    r = client.post("/api/fire", json={"file": "long_song.wav", "user": "bob"}).get_json()
    assert r["queued"]["file"] == "long_song.wav"
    r = client.post("/api/fire", json={"file": "long_song.wav", "user": "bob"}).get_json()
    assert len(r["queue"]["items"]) == 1                      # second one waits
    r = client.post("/api/fire", json={"file": "short_sound.wav", "user": "bob"}).get_json()
    assert r["fired"]["file"] == "short_sound.wav"            # sounds never queue


def test_tag_order_and_sections(app, album):
    o = app.tag_order("e")
    assert o["files"] == ["e1_horse.wav", "e2_just_a_man.wav", "e6_polyphemus.wav",
                          "e10_storm.wav", "epic_14_puppeteer.wav"]
    names = {s["name"]: s["file"] for s in o["sections"]}
    assert names["Troy"] == "e1_horse.wav" and names["Cyclops"] == "e6_polyphemus.wav"
    assert names["Ocean"] == "e10_storm.wav" and names["Circe"] == "epic_14_puppeteer.wav"


def test_play_from_section_to_the_end(app, editor_client, clock, album):
    r = editor_client.post("/api/queue/from", json={"tag": "e", "section": "cyclops", "user": "bob"}).get_json()
    assert r["ok"] and r["added"] == 3
    st = r["queue"]
    assert st["current"]["file"] == "e6_polyphemus.wav"
    assert [i["file"] for i in st["items"]] == ["e10_storm.wav", "epic_14_puppeteer.wav"]
    r = editor_client.post("/api/queue/from", json={"tag": "e", "file": "e10_storm.wav"}).get_json()
    assert r["queue"]["current"]["file"] == "e10_storm.wav"    # replace starts the new point now


def test_privileged_routes(app, client, editor_client):
    assert client.post("/api/queue/mode", json={"mode": "queue"}).status_code == 403
    assert client.post("/api/queue/clear").status_code == 403
    assert client.post("/api/queue/add", json={"files": ["long_song.wav"], "mode": "replace"}).status_code == 403
    tok = app._house_token()
    assert client.post("/api/queue/mode", json={"mode": "queue"},
                       headers={"X-House-Token": tok}).get_json()["ok"]
    assert editor_client.post("/api/queue/mode", json={"mode": "overlap"}).get_json()["ok"]


def test_node_targets_carried_on_entry(app, clock):
    app.queue_add(["long_song.wav"], "house", opts={"nodes": ["kitchen"], "boxes_only": True})
    e = next(a for a in app._ACTIVE if a.get("queue"))
    assert e["nodes"] == ["kitchen"] and e["boxes_only"] is True


def test_active_carries_queue(client):
    d = client.get("/api/active").get_json()
    assert d["queue"]["mode"] in ("overlap", "queue")
