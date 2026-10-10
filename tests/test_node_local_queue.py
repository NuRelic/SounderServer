"""The node's local queue (house voice presets: "Hey Siri, play EPIC").

The house Pi writes a queue file; the node streams those tracks in order on its own
speakers only. A song fired from the site must win, and the queue must pick the same
track back up afterwards. Deleting the file stops it.
"""
import importlib
import json
import pathlib
import sys
import types

import pytest


class FakeMusic:
    def __init__(self):
        self.loaded, self.busy, self.plays, self.stops, self.vol = None, False, 0, 0, None

    def load(self, p): self.loaded = p
    def play(self, *a, **k): self.plays += 1; self.busy = True
    def stop(self): self.stops += 1; self.busy = False
    def get_busy(self): return self.busy
    def set_volume(self, v): self.vol = v


@pytest.fixture
def agent(tmp_path, monkeypatch):
    music = FakeMusic()
    fake = types.ModuleType("pygame")
    fake.mixer = types.SimpleNamespace(init=lambda **kw: None, set_num_channels=lambda n: None, music=music)
    monkeypatch.setitem(sys.modules, "pygame", fake)
    monkeypatch.setenv("SS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SS_QUEUE_FILE", str(tmp_path / "q.json"))
    monkeypatch.setenv("SS_QUEUE_STATE", str(tmp_path / "state.json"))
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "deploy"))
    sys.modules.pop("kitchen_agent", None)
    ka = importlib.import_module("kitchen_agent")
    monkeypatch.setattr(ka, "ensure_cached", lambda fn, ver=0: "/cache/" + fn)
    ka._music = music
    ka._tmp = tmp_path
    return ka


def write_queue(ka, qid="a", n=3):
    (ka._tmp / "q.json").write_text(json.dumps(
        {"id": qid, "title": "EPIC", "tracks": [{"file": f"e{i+1}.mp3", "name": f"t{i+1}"} for i in range(n)]}))


def state(ka):
    return json.loads((ka._tmp / "state.json").read_text())


def test_plays_tracks_in_order_then_finishes(agent):
    write_queue(agent, n=2)
    agent.local_queue_tick(False, 0.7)
    assert agent._music.loaded == "/cache/e1.mp3" and state(agent)["index"] == 0
    agent.local_queue_tick(False, 0.7)              # still busy: nothing changes
    assert agent._music.plays == 1
    agent._music.busy = False                       # track 1 ends
    agent.local_queue_tick(False, 0.7)
    assert agent._music.loaded == "/cache/e2.mp3"
    agent._music.busy = False
    agent.local_queue_tick(False, 0.7)
    assert state(agent)["status"] == "done" and not (agent._tmp / "q.json").exists()
    assert agent._song_tok is None


def test_site_song_wins_and_queue_resumes_same_track(agent):
    write_queue(agent)
    agent.local_queue_tick(False, 0.7)
    agent._music.busy = False; agent.local_queue_tick(False, 0.7)    # now on track 2
    assert agent._music.loaded == "/cache/e2.mp3"
    agent._song_tok = 99                            # play_song() took the stream for a site song
    agent.local_queue_tick(True, 0.7)
    assert agent._music.loaded == "/cache/e2.mp3" and agent._song_tok == 99
    agent._song_tok = None                          # site song over, stopped by the main loop
    agent.local_queue_tick(False, 0.7)
    assert agent._music.loaded == "/cache/e2.mp3" and agent._lq["idx"] == 1


def test_deleting_the_file_stops_it(agent):
    write_queue(agent)
    agent.local_queue_tick(False, 0.7)
    (agent._tmp / "q.json").unlink()
    agent.local_queue_tick(False, 0.7)
    assert agent._song_tok is None and agent._music.stops >= 1 and state(agent)["status"] == "stopped"


def test_new_queue_replaces_old(agent):
    write_queue(agent, "a")
    agent.local_queue_tick(False, 0.7)
    write_queue(agent, "b")
    agent.local_queue_tick(False, 0.7)
    assert agent._lq["id"] == "b" and agent._music.loaded == "/cache/e1.mp3" and agent._music.plays == 2


def test_no_queue_file_is_a_no_op(agent):
    agent._song_tok = 5
    agent.local_queue_tick(False, 0.7)
    assert agent._song_tok == 5 and agent._music.stops == 0
