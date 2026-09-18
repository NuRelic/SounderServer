# A sound fired into a lane used to be DELETED from the active list the instant the next
# sound landed in that lane. Listeners (the room nodes, and every browser that did not
# fire it) only learn about a sound by polling /api/active a few times a second, so a fire
# that was replaced inside one poll interval was never audible anywhere except in the
# firing browser, which plays straight off the /api/fire response.
#
# Measured on the kitchen node before the fix: 5 fires into lane 0 at ~50ms spacing
# produced exactly 1 audible play, with no error and no warning logged anywhere.
#
# The lane contract itself is deliberate and stays: a new sound still interrupts the lane.
# The floor only guarantees an interrupted sound was on the wire long enough to be polled.
import time
import pytest


@pytest.fixture
def clock(monkeypatch):
    """A controllable wall clock, so a test can place fires 50ms apart without sleeping."""
    holder = {"t": 1_000_000.0}
    monkeypatch.setattr(time, "time", lambda: holder["t"])
    return holder


def _tokens(app):
    return [a["token"] for a in app.active_snapshot()]


def test_rapid_refire_into_same_lane_keeps_the_first_audible(app, clock):
    first = app.fire("short_sound.wav", "alice", lane=0)
    clock["t"] += 0.05                       # a double-click, far inside one poll interval
    second = app.fire("short_sound.wav", "alice", lane=0)
    assert _tokens(app) == [first["token"], second["token"]]


def test_five_rapid_fires_all_reach_listeners(app, clock):
    """The exact shape of the live repro that lost 4 of 5 plays on the kitchen box."""
    fired = []
    for _ in range(5):
        fired.append(app.fire("short_sound.wav", "alice", lane=0)["token"])
        clock["t"] += 0.05
    assert _tokens(app) == fired


def test_interrupt_still_replaces_a_sound_listeners_have_already_seen(app, clock):
    """The lane contract is a control the owner sets deliberately, so it must survive:
    past the visibility floor, a new fire still clears the lane."""
    app.fire("short_sound.wav", "alice", lane=0)
    clock["t"] += 1.0                        # everyone has polled it by now
    second = app.fire("short_sound.wav", "bob", lane=0)
    assert _tokens(app) == [second["token"]]


def test_a_held_sound_is_not_pinned_open_forever(app, clock):
    """The held entry must age out on its own, or a spammed lane would accumulate."""
    first = app.fire("short_sound.wav", "alice", lane=0)
    clock["t"] += 0.05
    second = app.fire("short_sound.wav", "bob", lane=0)
    assert first["token"] in _tokens(app)
    clock["t"] += 1.0                        # past the held expiry, still inside second's life
    assert _tokens(app) == [second["token"]]


def test_separate_lanes_are_untouched(app, clock):
    a = app.fire("short_sound.wav", "alice", lane=0)
    clock["t"] += 0.05
    b = app.fire("short_sound.wav", "bob", lane=1)
    assert _tokens(app) == [a["token"], b["token"]]


def test_a_sound_still_ends_at_its_natural_duration(app, clock):
    """Control: the floor must not extend a sound that nobody interrupted."""
    app.fire("short_sound.wav", "alice", lane=0)
    clock["t"] += 0.5
    assert len(_tokens(app)) == 1
    clock["t"] += 60.0
    assert _tokens(app) == []
