# A fired sound is only ever discovered by POLLING /api/active — every listener except
# the browser that fired it. So a sound is audible to the room only if it is still being
# advertised when the slowest listener next polls.
#
# tests/test_lane_visibility.py fixed that for the INTERRUPT path (MIN_VISIBLE). This file
# covers the other half, which that fix left open: a sound nobody interrupts, which simply
# ages out of _ACTIVE on its own at start + dur + pad.
#
# The floor was sized against the wrong number. server.py's own comment models the slowest
# listener as "browsers ~2.5x/s" (400ms), but templates/index.html throttles a BACKGROUNDED
# tab to one poll every 2500ms:
#
#     function activeInterval(){ return document.hidden ? 2500 : (_roomLive ? 400 : 1000); }
#
# A clip's whole advertised life is dur + SYNC_BUFFER + 0.6 = dur + 1.6s with sync on, so
# any clip under ~0.9s can begin and end entirely between two polls of a hidden tab and is
# never seen, never played, and never logged. 97 clips in the library are under 1s.
#
# This is duration-gated, which is exactly how it presents: "the shorter sounds don't play."
import os, struct, time, wave

import pytest

# templates/index.html activeInterval(): document.hidden -> 2500ms. This is the slowest
# poll any real listener runs, so it is the interval the server has to stay visible for.
HIDDEN_TAB_POLL = 2.5


@pytest.fixture
def clock(monkeypatch):
    """A controllable wall clock, so a test can place a poll exactly one cadence later."""
    holder = {"t": 1_000_000.0}
    monkeypatch.setattr(time, "time", lambda: holder["t"])
    return holder


def _add_clip(app, name, seconds):
    """Write a real wav of a given length into the library so duration() probes it."""
    path = os.path.join(app.SOUND_DIR, name)
    n = int(8000 * seconds)
    with wave.open(path, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(struct.pack("<" + "h" * n, *([0] * n)))
    app.scan_library()
    return name


def _tokens(app):
    return [a["token"] for a in app.active_snapshot()]


@pytest.mark.parametrize("dur", [0.2, 0.4, 0.6, 0.8])
def test_a_short_clip_is_still_advertised_when_a_hidden_tab_next_polls(app, clock, dur):
    """The worst case, and the common one: a listener polls, the clip is fired a moment
    later, and that listener does not poll again for a full cadence. The clip has to still
    be there or it was never playable for anyone but the person who clicked."""
    _add_clip(app, "blip.wav", dur)
    fired = app.fire("blip.wav", "alice", lane=0)
    clock["t"] += HIDDEN_TAB_POLL
    assert fired["token"] in _tokens(app), (
        "a %.1fs clip vanished before a backgrounded browser could poll it" % dur
    )


def test_a_long_clip_was_never_affected(app, clock):
    """Control: clips comfortably longer than the poll cadence were always fine, which is
    why this only ever presented as 'the SHORT sounds don't play'."""
    _add_clip(app, "longer.wav", 4.0)
    fired = app.fire("longer.wav", "alice", lane=0)
    clock["t"] += HIDDEN_TAB_POLL
    assert fired["token"] in _tokens(app)


def test_the_floor_does_not_pin_a_short_clip_open(app, clock):
    """Control: holding a clip visible must not make a lane look busy indefinitely — it
    still has to age out shortly after the slowest listener has had its chance."""
    _add_clip(app, "blip.wav", 0.3)
    app.fire("blip.wav", "alice", lane=0)
    clock["t"] += HIDDEN_TAB_POLL + 2.0
    assert _tokens(app) == []


def test_an_interrupt_still_clears_the_lane_promptly(app, clock):
    """Control: the visibility floor for a NATURAL expiry must not slow down a deliberate
    lane interrupt, which is a control the owner sets (see test_lane_visibility.py)."""
    _add_clip(app, "blip.wav", 0.3)
    app.fire("blip.wav", "alice", lane=0)
    clock["t"] += 1.0                       # past MIN_VISIBLE: everyone has polled it
    second = app.fire("blip.wav", "bob", lane=0)
    assert _tokens(app) == [second["token"]]
