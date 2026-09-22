# 2026-09-22 — "The shorter sounds aren't playing": two causes, neither one a dropped fire

Follow-up to `2026-09-18-silently-dropped-fires.md`. That one was a real drop (the server
deleted a fire before anyone polled it). This one presents **identically to the person in
the room** — "I click and nothing happens, but only on the short ones" — and is not a drop
at all. The 09-18 measurement is still the right first move, and here it came back clean,
which is the finding.

---

## 1. Symptom

Short clips seemed not to play, in the kitchen **and** in a browser, mostly while a song
was playing. Longer clips were fine.

## 2. Start with the 09-18 count — a clean result is information

```bash
curl -s https://sounderserver.party/api/feed -o /tmp/feed.json
ssh pi@soundboard 'journalctl -u sound-node --since "-4 days" -o short-iso | grep "▶"' > /tmp/played.txt
```

Counted per name over the strict overlap: **298 fired / 298 played over 94h**, 0% missed in
every duration bucket from 0-0.5s up. The only miss in the window was one 208s song
interrupted by another song, which is the lane contract working.

**So nothing was being dropped, and the box was playing every single short clip.** That
rules out the entire 08-26 and 09-18 families in one command and says the problem is
either *audibility* or *a listener that isn't the box*. Both turned out to be true.

## 3. Cause A — clips had no headroom over the music (explains the box)

The node's mix levels made a clip and the music it plays over land at **exactly the same
amplitude**:

| | song alone | song under a clip | the clip |
|---|---|---|---|
| before | `vol × 0.7` | `vol × 0.6` | `vol × 0.6` |

That "duck" is 0.7 → 0.6, i.e. **−1.3 dB**, and the clip carried the *same* 0.6 factor, so
a clip played at **+0.0 dB over the music**. `SS_SONG_DUCK`'s own comment described this as
intended ("only a slight dip so song + clip play at comparable volume") — comparable volume
is the bug.

Measured the library to check whether short clips are also quieter at source. They are not
**shorter**-quieter — pearson r between duration and mean dBFS is **−0.013**, no relationship,
and the 0-0.5s band is actually the loudest. But the absolute level matters: **61% of short
clips sit below the songs' own level (−12.1 dBFS)** before any gain is applied.

So why only the *short* ones? Because level is only half of audibility. A 5s clip at the
music's level still reads — you get seconds to pick it out. A 0.4s blip at or under a
continuous bed is masked and never reaches you as a distinct event. The duration
dependence is perceptual; the defect is the missing headroom.

**Fix:** `SS_SONG_DUCK` 0.6 → 0.25 (a ~9 dB duck under the 0.7 baseline, the normal range
for putting a voice over music) and `SS_SOUND_GAIN` 0.6 → 0.85. Clips now sit **+10.6 dB**
over the ducked music instead of +0.0. Both stay env-tunable per node.

The browser had it worse: **no song/clip distinction at all**, one shared `MASTER` gain for
everything, so a clip could never be anything but exactly as loud as the song. Songs now
route through a `SONGBUS` gain node and each clip holds a duck while it actually sounds
(held by the element's own `playing`/`ended`, not by `/api/active`, so the duck lasts the
clip's audio and not its whole advertised lifetime). The failure mode to watch is a
**stuck** duck — `tests/duck_bookkeeping.test.js` covers every release path and overlap.

## 4. Cause B — a backgrounded tab literally never saw them (explains the browser)

`MIN_VISIBLE` from 09-18 floored the **interrupt** path. A clip nobody interrupts doesn't
take that path at all: it just ages out at `start + dur + pad`, which with sync on is
`dur + 1.6s`. Fine for every listener the server thought it had — its own comment models
them as "the room nodes every 0.35s, browsers ~2.5x/s".

That model is wrong by 6x, because the frontend throttles a **hidden** tab:

```js
function activeInterval(){ return document.hidden ? 2500 : (_roomLive ? 400 : 1000); }
```

A clip under ~0.9s has a shorter life than one poll of a backgrounded tab, so it can begin
and end entirely between two polls: never seen, never played, never logged. Exactly
duration-gated, and 97 clips in the library are under 1s. This is the same bug as 09-18 —
an advertised lifetime shorter than a listener's poll — just on the other code path, and
against a listener nobody had counted.

**Fix:** `MIN_ADVERTISED` (`SLOWEST_POLL` 2.5s + 0.4 margin). Every fire stays on
`/api/active` for at least one full cycle of the slowest real poll. Long sounds are
untouched — their own `dur + pad` already clears it. Interrupts stay on the tighter
`MIN_VISIBLE` floor, because an interrupt is a deliberate cut and *should* be prompt.
Covered by `tests/test_short_clip_visibility.py`, with controls for the long-clip case,
for not pinning a lane open, and for the interrupt path staying fast.

**If you change `activeInterval()` in the frontend, change `SLOWEST_POLL` to match.**

## 5. What this rules in and out next time

| Symptom | Look at |
|---|---|
| Box online, plays nothing at all | 2026-08-26 doc. Reboot the gateway first. |
| Box plays some, skips others, logs clean, **fired > played** | 2026-09-18 doc. A real drop. |
| **fired == played but you didn't hear it** | This doc §3. It's level, not delivery. Check the duck. |
| Only short clips, and only in a browser | This doc §4. Compare the clip's life (`dur + 1.6s`) against `activeInterval()`. |
| Only over music | This doc §3. |

The trap here is that "some sounds don't play" reads as a delivery fault, so both previous
runbooks point at transport. Count first: **if fired == played, stop looking at the network.**
