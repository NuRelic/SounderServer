"""Queue-mode seek reaches the room nodes: a changed `start` for the same token restarts the
stream at the new offset; entries aimed at other rooms are ignored."""
import time

from tests.test_node_local_queue import agent, FakeMusic  # noqa: F401  (fixture reuse)


class SeekMusic(FakeMusic):
    def __init__(self):
        super().__init__(); self.starts = []
    def play(self, *a, **k):
        super().play(*a, **k); self.starts.append(k.get("start", 0))


def test_seek_restarts_stream_at_new_offset(agent, monkeypatch):
    m = SeekMusic(); agent.pygame.mixer.music = m
    now = time.time()
    e = {"token": 7, "file": "song.mp3", "name": "song", "start": now - 1, "dur": 300, "lane": "song0"}
    agent.play_song(e, 0.7)
    assert m.plays == 1
    agent.play_song(dict(e), 0.7)                         # same start: nothing happens
    assert m.plays == 1
    agent.play_song(dict(e, start=now - 120), 0.7)        # seeked to ~2:00
    assert m.plays == 2 and 118 < m.starts[-1] < 122
