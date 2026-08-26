# 2026-08-26 — Living-room node silence: incident, fixes, and runbook

A full record of a long debugging session, for whoever (human or agent) touches the
playback nodes next. Covers the root cause, every dead end (so you don't repeat them),
the code changes shipped, how remote access was finally obtained, and the operational
gotchas that will bite again.

The two playback nodes both run `deploy/kitchen_agent.py` as a systemd unit:
- **kitchen** — `pi@soundboard` on the owner's LAN (also runs `ss-download-worker`, the
  residential yt-dlp worker the website depends on — never disturb it).
- **livingroom** — `soundnode-livingroom`, a Pi 4 in a friend's house behind T-Mobile
  5G Home Internet (Inseego Wavemaker FX4100 gateway).

---

## 1. The symptom and the (wrong) theories we burned time on

The living-room node showed **online** in the server's `/api/active` list but played
**no sound**. Hours went into physical audio debugging that was all a dead end:

- **WRONG: bad speaker cable / wrong 3.5mm jack.** Had the occupant swap the speaker
  plug between the USB dongle and the Pi's onboard jack repeatedly. Pointless.
- **WRONG: agent grabbed the wrong ALSA device (HDMI/onboard vs USB).** The unit was
  correctly pinned `SS_AUDIODEV=hw:CARD=Device,DEV=0`, and `aplay -l` showed card 1 =
  `Device [USB Audio Device]`. Audio config was correct the whole time.
- **Key realization that should have come first:** `pygame.mixer.init()` runs at module
  scope in `kitchen_agent.py`, *before* the poll loop. If the audio device were wrong the
  process would die on import and the node would never appear online at all. It WAS
  online → the mixer opened fine → the problem is upstream of audio.

**The diagnostic that actually works:** fire a **zero-play** song (one never pre-warmed,
so it cannot be cached) and watch the node's TCP connections to the VPS. If no new
connection ever opens, the node isn't fetching audio at all — stop looking at speakers.
Crucially, watch **all** TCP states (`ss -tan`), not just `established` — a failed
handshake never reaches ESTAB and is invisible to `ss ... state established`.

## 2. Root cause: NAT-table exhaustion on the FX4100

Confirmed from the VPS side while the node was silent:
- `ss -tan` toward the node's public IP showed **3–7 stuck `SYN-RECV`** at all times.
- Kernel counters: `SyncookiesSent 26 / SyncookiesRecv 0`, **>1,000,000 `TCPSynRetrans`**,
  and **`ListenOverflows: 0`** (so the VPS was healthy — nothing to tune server-side).

New TCP connections from the gateway were dying mid-handshake. The agent's **poll socket
is a long-lived keep-alive**, so it survived and kept the node looking "online," while
**every new connection — i.e. every audio download — silently failed.**

**Fix that worked: reboot the gateway.** That flushed the NAT table and downloads
resumed instantly. **Rule for next time: a node that is online but silent → reboot the
ROUTER before touching the Pi.** (The gateway gets a new CGNAT public IP after a reboot,
so any IP-based monitoring must be re-identified.)

## 3. The code bugs that turned a blip into permanent silence

A transient NAT problem should cause a few late sounds, not indefinite silence. Two bugs
made it permanent (both fixed 2026-08-26, shipped to `origin/main`):

- **`_download` had no DNS fallback.** Polls fall back from the origin-pin to DNS via
  `_poll_opener()` after repeated failures; downloads used `_opener_dl` (origin-pinned)
  **unconditionally**. So once direct-to-origin connections broke, polling limped along on
  its existing socket while downloads failed forever with no recovery path.
  Fix: `_dl_opener()` + a shared `_note_pin_failure()`/`_note_pin_ok()` so both the poll
  loop and the download worker trip the same DNS-fallback cooldown (commit `11f24d7`).
- **0-byte cache files counted as cached forever.** `ensure_cached` used
  `os.path.exists`, so a truncated/empty download satisfied the cache check permanently
  and was never re-fetched — it just silently failed to play. (59 such files had been
  hand-cleaned on 2026-08-22; the root cause was only fixed now.)
  Fix: size check in `ensure_cached` (delete 0-byte, re-download), `Content-Length`
  validation before `os.replace` in `_download`, and a startup sweep that removes any
  0-byte cache file on boot (commit `11f24d7`).

## 4. Everything else shipped 2026-08-26 (commits on `origin/main`)

| SHA | What |
|---|---|
| `11f24d7` | node: download DNS fallback + 0-byte cache guards + startup sweep |
| `8faff59` | prewarm: cache every SHORT clip first, then recent songs, then the rest |
| `99fdba8` | web: clips < 5s always play from 0 (no sync-seek, no drift correction) |
| `1bbf542` | setup_node.sh: don't abort when run from the install dir (pre-existing) |
| `99f5d8a` | docs: manifest of the legacy kitchen library before deletion |

Rationale worth keeping:
- **Shorts-first prewarm:** a short clip lives only ~2.6s server-side, so a cache miss
  means it is *never* played (unrecoverable). A song miss merely starts late and
  self-heals. All 1,178 shorts total only ~430 MB, so cache them unconditionally first.
- **Clips < 5s play instantly:** seeking into a short clip to "catch up" to the shared
  timeline ate most of the audio (a 0.9s fetch on a 1.2s clip left 0.3s); the drift loop
  then yanked `currentTime` mid-playback. Under `INSTANT_THRESHOLD` (server.py) / matching
  `INSTANT_MAX` (index.html) they play from 0 and are skipped by the drift loop — which is
  what the room boxes already did, so browsers now match them.
- **New `/api/sounds` field `last`** = last-played unix ts (from `sound_stats_all_time.
  last_update`), which the recency-tiered prewarm needs.

## 5. Node end-state (both, after this session)

Both: agent fixed, `NRestarts=0`, **cache cap 6144 MB**, cache **~4.1 GB / ~2019 files,
100% historical-play coverage, 0 zero-byte**, on Tailscale.

Kitchen extras: unified onto the same `sound-node.service` as every room (was a bespoke
`kitchen-agent.service`); legacy `soundboard`/`normalize`/`*-watchdog`/`*-web` units
disabled; the **13 GB legacy button-soundboard library at `~/soundboard/sounds` was
deleted** (manifest saved: `docs/legacy_kitchen_sounds_manifest.txt`), taking disk from
80% → 33%. `ss-download-worker` left untouched. Deploy verified with a live download of a
zero-play song.

## 6. Remote access — how it was finally obtained

The Mac is **not** on the tailnet. Reach the living room by hopping through the kitchen:
```
ssh pi@soundboard 'tailscale ssh pi@100.119.1.1 "<cmd>"'
```
- Tailnet: `kitchen-pi` = 100.114.58.15, `livingroom-pi` = 100.119.1.1 (account bnowlin6@).
- **Plain `ssh -J` ProxyJump does NOT work** — `tailscale up --ssh` makes Tailscale
  intercept port 22 with its own SSH server. Use `tailscale ssh`.
- **`tailscale ssh` "check mode":** the default SSH policy is `action:"check"`, so it
  periodically prints a `login.tailscale.com/a/...` URL the USER must click to authorize.
  Set the own-devices SSH rule to `action:"accept"` in Access Controls to stop the prompt.

**How Tailscale got bootstrapped past CGNAT (the trick that finally worked):** the FX4100
hands out a **global IPv6** (`2607:FB90::/32`, T-Mobile) which has no NAT. The occupant's
phone, on **cellular with WiFi OFF** (native IPv6), SSH'd to the Pi's temporary SLAAC v6
address on port 22 via the Termius app with the private key imported, then typed the two
`tailscale up` commands. Everything IPv4 failed: `.local` didn't resolve, the raw LAN IP
timed out (WiFi **client isolation** on the gateway), and inbound IPv4 is CGNAT-blocked.
The Mac has no IPv6 route and the Vultr VPS had no IPv6 subnet assigned, so only the
phone-over-cellular path worked. This is a one-shot bootstrap door (SLAAC addr rotates,
prefix changes on gateway reboot) — the point was to install Tailscale, which then
survives all of that.

## 7. The FX4100 gateway is programmable (ubus / JSON-RPC)

The admin UI is a Flutter SPA over an OpenWrt **ubus** backend. From the living-room Pi
(on the gateway's LAN) you can drive it directly — this is how the WiFi was changed.

- Endpoint **`https://192.168.1.1/ubus`** — **HTTPS only** (`/cgi-bin/*` is blocked over
  HTTP; the `/cgi-bin/cgi-exec` endpoint is a red herring, ignore it). Send `-k`, a
  browser `User-Agent`, and `Referer: https://192.168.1.1/`.
- **Login:** `call ["<32 zeros>", "webui.login", "authenticate", {"password":"<admin>"}]`
  → `result:[0,{"authenticated":1,"session_token":"<hex>"}]`. Use that token as
  `params[0]` for every later call. Token expires ~600s.
- `list ["*"]` enumerates 897 objects but the response truncates JSON parsers at ~12KB —
  grep the raw text instead of `json.load`.
- **WiFi lives on `sysinterface.wifi`:** `get_advanced_settings` /
  `set_advanced_settings` — **must send all six fields** (`wifi_2g_standard/channel/
  bandwidth`, `wifi_5g_standard/channel/bandwidth`) or you wipe the omitted ones;
  `channel:0` = Auto. DFS toggle: `get_wifi_dfs_enabled` / `set_wifi_dfs_enabled
  {enabled:0|1}`. Channel list: `get_wifi5_chlist`. Setter OK = `result:[0]`.
- **Reboot:** `sysinterface.device.reboot_system` (immediate). There is **no local
  scheduled-reboot** feature; `inseegoconnect.set_ic_reboot_enabled` is an Inseego
  **cloud** feature (unknown schedule/behavior) — left disabled deliberately, and the
  download DNS-fallback fix already covers the NAT-exhaustion case it would address.

**Changes applied 2026-08-26:** **DFS disabled** so Auto can never sit on a radar
(DFS) channel again — this was the fix for the random dropouts (radar eviction forces a
60s channel vacate). 5G channel was set to 149 but reverted to Auto and landed on **157**
(non-DFS) — fine either way. Settings persisted across a gateway reboot.

### ⚠️ The big operational trap (cost us a 19-minute outage)

Changing the 5 GHz channel restarts the radio, and **the Pi's Broadcom WiFi did NOT
rejoin on its own — it was offline ~19 minutes and only recovered when the occupant
POWER-CYCLED the gateway.** And because **the only route to the gateway is THROUGH that
Pi**, once the Pi drops you lose gateway access too and cannot revert remotely. So:
**do not change the gateway WiFi channel remotely unless someone is on-site to reboot it.**
Pair any such change with a person in the house.

## 8. WiFi health + node-side tuning (2026-08-26)

Their **WiFi is excellent** — RSSI -42 dBm, 5 GHz 80 MHz, zero retries, zero loss, ~3ms
to the gateway. The real bottleneck is the **5G uplink: RSRP -96 dBm** (fair-to-weak),
which is a gateway *placement* problem, not a WiFi one. Don't let anyone buy a mesh system
to fix what is an uplink-signal issue — repositioning the FX4100 toward a window is the win.

Applied on the Pi:
- **WiFi power-save OFF** (`iw dev wlan0 set power_save off`, persisted via
  `nmcli con modify FX4100-6090 802-11-wireless.powersave 2`). Measured effect: gateway
  latency jitter 4.59ms → 0.12ms, max 21ms → 3.45ms.
- **`netmon.timer`** (systemd) samples RSSI / link speed / gateway + internet latency &
  loss every 5 min into `/home/pi/netmon.csv` (self-trims ~2 months). Turns "the wifi's
  been slow" into a chart. Script: `/usr/local/bin/netmon.sh`.

## 9. Quick runbook for "the living room node is silent again"

1. Is it in the server's online list? (`/api/active`). If not, its WiFi/tailnet dropped —
   wait or have someone reboot the gateway.
2. If online but silent: **reboot the gateway first** (NAT exhaustion is the likely
   cause). Have the occupant power-cycle the FX4100.
3. Confirm downloads work: from the kitchen, `ssh pi@soundboard 'tailscale ssh
   pi@100.119.1.1 "journalctl -u sound-node -n 20 --no-pager"'` and fire a zero-play song
   from the VPS; you should see a `cached ...` line then `▶`.
4. Cache/agent health on the box: `du -sh ~/kitchen_cache`, `find ~/kitchen_cache -size 0`
   (should be 0 now that the sweep exists), `systemctl show sound-node -p NRestarts`.
5. Network history: `/home/pi/netmon.csv`.
6. **Never** change the gateway WiFi channel remotely without someone on-site (see §7).

## 10. Credentials & pointers (kept in the auth-restricted memory, not here)

Gateway admin password, security password, and the Pi console password are recorded in
the persistent memory file `project_livingroom_node.md` (and the console password lives in
`~/pi-node/pi_console_password.txt` on the owner's Mac). They are intentionally NOT dumped
into this repo doc. The owner's `~/.ssh/id_ed25519` is the only key in the box's
`authorized_keys`; SSH there is key-only.
