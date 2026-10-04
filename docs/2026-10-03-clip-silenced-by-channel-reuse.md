# 2026-10-03 — "I hear the song duck but I don't hear the clip": a stop() aimed at the wrong clip

Third entry in the "short clips aren't playing" family, after `2026-09-18-silently-dropped-fires.md`
(a real drop) and `2026-09-22-short-clips-not-playing.md` (level, plus a hidden tab). This one is
neither a drop nor a level problem: the node **played the clip and then stopped it itself**, a
fraction of a second later, on behalf of a different clip that had already finished.

---

## 1. Symptom

> "The sounds are not always playing when a song plays. I hear the song lower but I don't hear
> the sound." — more often on the boxes than in a browser.

The reporter's phrasing is the whole diagnosis if you listen to it: **the duck fires.** The song
audibly drops, which means the node saw the clip in `/api/active`. So this is not delivery, and it
is not the 09-18 family. Something between "saw it" and "heard it" threw the audio away.

## 2. What the usual checks said — all green, all misleading

| Check | Result | What it seemed to rule out |
|---|---|---|
| `md5sum` Pi vs repo `kitchen_agent.py` | identical | stale deploy |
| uncached shorts (computed `md5("<file>@<ver>")` vs `ls ~/kitchen_cache`) | **0 of 1178** | `ensure_cached()` returning None |
| `grep "play error"` over 4 days | **0 hits** | `Sound()` load / `ch.play()` failure |
| fired vs played, strict overlap | 61 shorts fired, 76 played lines | a wholesale drop |
| `⚠ blind for N.Ns` | 3 in 4 days (2.9s, 6.0s, 4.2s) | too rare to be the complaint |

The 5 fired-but-unplayed shorts (`cable` 1/0, `chewy` 5/4, `no` 5/2) are a **separate, smaller**
issue — blind windows and two service restarts that afternoon. They cannot be this symptom,
because a clip the node never saw would not have ducked the song either. That asymmetry is what
sent the investigation back to the code instead of the network.

## 3. Root cause — `_playing` outlives the channel, and the channel gets recycled

Two lifetimes that look the same and aren't:

- **A clip's audio** ends when the sound ends — typically 0.3–2s. pygame frees the channel then.
- **A clip's token** stays in the node's `_playing` dict until the SERVER stops advertising it,
  which is at least `MIN_ADVERTISED` = `SLOWEST_POLL` (2.5s) + 0.4 ≈ **2.9s** (see the 09-22 doc,
  §4 — that floor exists so a backgrounded tab still gets one poll).

So for ~1–2.5s a token sits in `_playing` holding a `(channel, Sound)` pair whose channel pygame
has already handed back to the pool. `pygame.mixer.find_channel()` returns the **lowest-index idle
channel**, so the next clip fired does not get a random channel — it very often gets *that exact
one*. Then the first token ages out and the cleanup loop ran:

```python
for tok in list(_playing):
    if tok not in live:
        ch, _ = _playing.pop(tok)
        try: ch.stop()          # <-- stops whatever is on that channel NOW
        except Exception: pass
```

`ch.stop()` does not stop "clip A". It stops **channel 0**, and channel 0 is now playing clip B.

Reproduced against real pygame on the Pi (`SDL_AUDIODRIVER=dummy`, so the live agent's DAC is
untouched):

```
t=0.0  clip A -> channel 0, busy=True
t=1.0  A audio busy=False   (A still advertised, so still in _playing)
t=1.0  clip B -> channel 0  <- find_channel returns the SAME channel
t=1.0  A's channel object now holds: B
after A cleanup -> B still audible: False
*** BUG REPRODUCED: A's cleanup silenced clip B ***
```

Not a race — deterministic for the ordinary pattern "fire a clip, fire another within ~2.9s".

**Why it hid so well.** Every instrument pointed the wrong way:
- the node logs `▶` for B *before* B is killed, so **fired == played** and the 09-22 runbook's
  count comes back clean;
- nothing raises, so there is no `play error:` line;
- the duck keys off `shorts` being non-empty in `/api/active`, **not** off any clip actually
  sounding — so the song ducks for a clip that is silent, which is exactly the reported symptom.

**Why "worse over a song"** is mostly perceptual plus behavioural: the duck makes the failure
*audible as an event* (you hear the music dip for nothing), and people fire clips in bursts while
music is on, which is precisely the pattern that collides. How much of B gets cut depends on when
B was fired inside A's remaining advertised life — fire B late in that window and B is cut almost
immediately, which reads as "it didn't play at all".

**Why the boxes and not really the browser:** this is channel-pool bookkeeping, which only the node
has. The browser gives every token its own `<audio>` element and holds its duck on the element's
own `playing`/`ended` events (09-22, §3), so it has no equivalent.

## 4. Fix

`_release_finished(playing, live)` — stop a channel only if it is still playing *that token's own*
Sound:

```python
ch, snd = playing.pop(tok)
try:
    if ch.get_sound() is snd:
        ch.stop()
except Exception:
    pass
```

Identity, not equality: two `Sound`s loaded from the same file are distinct objects, so `is` asks
the right question. The pop happens before the comparison on purpose — a channel that raises must
not strand its token in `_playing`, because `play_short()` early-returns on `tok in _playing` and a
leak would mute that token for the rest of the process's life.

`NODE_VERSION` → `2026.10.03`, so `/api/nodes` shows which boxes have it.

Covered by `tests/test_node_channel_release.py`: the regression, plus controls for the normal
interrupt path still stopping, for live tokens being untouched, and for a raising channel not
aborting the sweep. The suite stubs `pygame` (a Pi-only dep) in `sys.modules`, since the logic is
pure bookkeeping over duck-typed channel objects.

Verified afterwards against real pygame on the Pi with the same scenario: `B playing after
cleanup: True`, and the control (a clip still on its own channel) still stops.

## 5. What this rules in and out next time

Add to the 09-22 table:

| Symptom | Look at |
|---|---|
| **The song ducks and you hear no clip at all** | This doc. The duck proves the node SAW it, so stop checking delivery. Suspect bookkeeping that outlives the audio. |

The trap, three docs running, is that "the sound didn't play" keeps reading as a transport fault.
The 09-22 lesson was *count first*. The lesson here is one level up: **ask which component the
symptom proves is already working.** A duck with no clip proves discovery, delivery, caching and
decode all succeeded, and that single observation skips every check in §2.

A standing hazard worth naming: the duck is driven by `/api/active` and not by audio, so **the node
will duck for a clip it fails to play, by any future mechanism too.** If a silent-clip report ever
comes back, that indirection is the first thing to re-examine.
