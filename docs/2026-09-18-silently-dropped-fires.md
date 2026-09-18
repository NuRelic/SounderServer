# 2026-09-18 — "Some sounds don't play on the box": interrupts beating the poll

Companion to `2026-08-26-node-silence-incident-and-fixes.md`. That one covers a node that
goes **totally** silent. This one covers the much more confusing case: the box plays most
things but quietly skips some, with **nothing in any log** on either side.

**If the complaint is "some sounds didn't play", do NOT start rebooting routers.** The
2026-08-26 runbook is for total silence and will waste your time here.

---

## 1. Symptom

The kitchen node played most fires and silently dropped others. No `play error`, no
`download error`, no `⚠ blind` at the relevant times, `NRestarts=0`, cache complete,
network clean. The person clicking heard every single click, which is what makes this
read as "the box is broken".

## 2. The measurement that localises it in one shot

Compare what the server **fired** against what the node **played**, by name, over a long
window. Do not eyeball the journal.

```bash
curl -s https://sounderserver.party/api/feed  -o /tmp/feed.json      # fired (3-day TTL)
ssh pi@soundboard 'journalctl -u sound-node --since "-3 days" -o short-iso | grep "▶"' \
  > /tmp/played.txt                                                  # played
```

Then count per name over the strict overlap of the two windows. **Count, do not pair.**
A greedy one-to-one match on rapid duplicates mis-attributes and invented 6 phantom
misses on the first attempt here.

Result that cracked it: 208 fired / 203 played, and the entire gap was **two** sounds,
both spam-clicked (`turtle` 21→18, `fixit` 12→9). Every other sound matched exactly.
A fault that is concentrated on the spammed sounds is not a network fault.

## 3. Root cause

`fire()` in `server.py` gave each lane exactly one sound and **deleted** the previous
occupant outright:

```python
for a in [x for x in _ACTIVE if x.get("lane") == lane]:   # interrupt this lane
    _ACTIVE.remove(a)
```

Listeners are **poll-only**: the room nodes read `/api/active` every 0.35s, browsers about
2.5x/s. But the browser that FIRES a sound does not wait for a poll, it plays straight off
the `/api/fire` response (`if(d.ok && d.fired) startEntry(d.fired)`).

So a sound replaced in its lane in under one poll interval was played for the clicker and
for **nobody else, anywhere**. It never had to fail: it was deleted before it could be seen.

Live repro before the fix: **5 fires into lane 0 at ~50ms spacing produced 1 audible play**,
with `active_now` stuck at 1 after every fire.

Aggravating factor, not a bug: the lane count is an owner-facing control and had been
turned down from 4 to 2, which doubles the collision odds. A 13-click `jabroni` burst that
happened to spread across lanes 0-3 lost nothing.

## 4. Fix

`MIN_VISIBLE` (env `SS_MIN_VISIBLE`, default 0.8s) in `server.py`. An interrupted sound
younger than the floor is not removed; it gets an `expire_at` and `_prune_locked` drops it
once it has been on the wire long enough for every listener to have polled it at least
once. Anything older is removed immediately, exactly as before.

**The lane contract is unchanged and deliberate: a new sound still interrupts the lane.**
The floor only stops the interrupt from outrunning the poll. Covered by
`tests/test_lane_visibility.py`, including controls asserting that a sound listeners have
already seen IS still replaced, and that a held sound is not pinned open.

Verified end to end after deploy: the same 5-fire burst now produces 5 plays on the box.

## 5. Also fixed the same day (node side, `deploy/kitchen_agent.py` 2026.09.18)

- **Pin fallback was far too slow.** `PIN_FAIL_LIMIT` 5 at a 3s poll timeout meant ~17s of
  total blindness before the agent even *tried* DNS, and discovery is poll-only, so every
  sound in that window was lost. Four separate 14-25s blind windows in 24h on the kitchen,
  each one a run of five timeouts ending in exactly that fallback. Now `POLL_TIMEOUT` 2.0s
  and `PIN_FAIL_LIMIT` 2 (`SS_POLL_TIMEOUT` / `SS_PIN_FAIL_LIMIT`), so worst case is ~5s.
  Healthy nodes measure ~15ms internet latency, so 2s is ~30x headroom.
- **Clean shutdown.** The poll loop had no exit and SDL holds a non-daemon audio thread, so
  the agent ignored SIGTERM until `TimeoutStopSec` fired and systemd SIGKILLed it: a 10s
  stall on every restart, and a kill that can land mid-download. Restart measured 10.1s →
  0.5s, no forced kill.
- **Startup sweep also clears `*.tmp` part-files.** A download writes `<cache>.<pid>.tmp`
  and only `os.replace`s it once Content-Length checks out, so a killed node stranded one
  under a pid that will never return. Invisible to `ensure_cached`, collected by nothing,
  counted against the cache cap forever.

## 6. What this rules in and out next time

| Symptom | Look at |
|---|---|
| Box online, plays nothing at all | 2026-08-26 doc. Reboot the gateway first. |
| Box plays some things, skips others, logs clean | This doc. Count fired vs played. |
| `⚠ blind for Ns` in the journal | Transport. Check `on_dns_fallback` in `/api/nodes` and `netmon.csv`. |
| A named sound never plays, ever | Cache. Diff library keys against the box's cache dir. |

Cache diff, which ruled cache out here in one command (all 1177 shorts present, correct
versions): compute `md5("<file>@<ver>").hexdigest() + ext` for every row of `/api/sounds`
and compare against `ls ~/kitchen_cache`.
