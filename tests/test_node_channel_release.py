"""The node must never stop a channel a NEWER clip has already taken over.

Regression test for 2026-10-03, "I hear the song duck but I don't hear the clip".

pygame frees a channel as soon as a clip's audio ends, but the node keeps the token
in `_playing` until the SERVER stops advertising it — at least `MIN_ADVERTISED`
(~2.9s), which is far longer than most clips sound for. `find_channel()` returns the
lowest-index idle channel, so the next clip fired almost always inherits the previous
clip's channel. The old cleanup called `ch.stop()` unconditionally when a token aged
out, which cut off whatever was on that channel *now* — the newer clip. Reproduced
against real pygame on the Pi: clip A on channel 0, clip B fired 1s later also lands
on channel 0, and A's cleanup silenced B.

It was invisible from every angle: the node logged a `▶` for B (it genuinely started),
no exception was raised, and the song still ducked, because the duck is driven by a
short being present in `/api/active` and not by any clip actually sounding.
"""
import importlib
import pathlib
import sys
import types

import pytest


def _load_agent(tmp_path, monkeypatch):
    """Import deploy/kitchen_agent.py with pygame stubbed out.

    pygame is a Pi-only dependency (deploy/requirements-pi.txt) and the module calls
    mixer.init() at import, so it can't be imported on a dev box or in CI as-is. The
    bookkeeping under test only ever calls get_sound()/stop() on duck-typed channel
    objects, so a stub costs the test nothing in fidelity.
    """
    fake = types.ModuleType("pygame")
    fake.mixer = types.SimpleNamespace(init=lambda **kw: None,
                                       set_num_channels=lambda n: None)
    monkeypatch.setitem(sys.modules, "pygame", fake)
    monkeypatch.setenv("SS_CACHE_DIR", str(tmp_path / "cache"))
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "deploy"))
    sys.modules.pop("kitchen_agent", None)
    return importlib.import_module("kitchen_agent")


@pytest.fixture
def agent(tmp_path, monkeypatch):
    return _load_agent(tmp_path, monkeypatch)


class FakeChannel:
    """Stands in for pygame.mixer.Channel: holds whichever Sound played last."""

    def __init__(self):
        self.sound = None
        self.stops = 0

    def play(self, snd):
        self.sound = snd

    def get_sound(self):
        return self.sound

    def stop(self):
        self.stops += 1
        self.sound = None


def test_aged_out_clip_does_not_stop_the_clip_that_reused_its_channel(agent):
    """The actual bug: A ages out, but B is on A's old channel and must keep playing."""
    ch = FakeChannel()
    snd_a, snd_b = object(), object()

    ch.play(snd_a)                      # clip A fires, takes channel 0
    playing = {"A": (ch, snd_a)}

    # A's audio ends; pygame frees the channel while the server still advertises A.
    # Clip B fires and find_channel() hands back that same channel.
    ch.play(snd_b)
    playing["B"] = (ch, snd_b)

    agent._release_finished(playing, {"B"})     # A left /api/active, B still live

    assert ch.stops == 0, "A's cleanup stopped the channel B had taken over"
    assert ch.get_sound() is snd_b, "B's audio was cut off by A's cleanup"
    assert "A" not in playing, "A should still be dropped from the bookkeeping"
    assert "B" in playing, "B is still live and must be kept"


def test_aged_out_clip_still_stops_its_own_channel(agent):
    """Control: the interrupt/stop path must keep working for the normal case."""
    ch = FakeChannel()
    snd = object()
    ch.play(snd)
    playing = {"A": (ch, snd)}

    agent._release_finished(playing, set())

    assert ch.stops == 1, "a clip still holding its own channel must be stopped"
    assert playing == {}


def test_live_tokens_are_left_alone(agent):
    """A clip the server is still advertising must not be touched at all."""
    ch = FakeChannel()
    snd = object()
    ch.play(snd)
    playing = {"A": (ch, snd)}

    agent._release_finished(playing, {"A"})

    assert ch.stops == 0
    assert ch.get_sound() is snd
    assert "A" in playing


def test_one_bad_channel_does_not_abort_the_sweep(agent):
    """A channel that raises must not strand the remaining tokens in _playing.

    _playing is the node's only record of what it has running; a leak here would
    make play_short() skip that token forever (it early-returns on `tok in _playing`).
    """
    class Exploding(FakeChannel):
        def get_sound(self):
            raise RuntimeError("mixer went away")

    good = FakeChannel()
    snd_good = object()
    good.play(snd_good)
    playing = {"bad": (Exploding(), object()), "good": (good, snd_good)}

    agent._release_finished(playing, set())

    assert playing == {}, "a raising channel left tokens stuck in _playing"
    assert good.stops == 1
