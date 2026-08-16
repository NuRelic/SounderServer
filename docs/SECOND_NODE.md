# Adding a playback node in someone else's house

A "node" is a cheap box that runs `deploy/kitchen_agent.py`: it polls the server and
plays whatever is firing, through speakers in that room. No buttons, no screen, no
web UI, no password. This is exactly what the kitchen does, minus the GPIO buttons.

Provisioning is one command — `deploy/setup_node.sh` — after a normal Raspberry Pi
OS Lite install.

## How much machine you need

Almost none. On the kitchen Pi 4 the agent uses **3% of one core and 42 MB of RAM**.
The real requirements are: an audio output, WiFi, and enough disk for the audio cache.

## Shopping list

Prices are rough US ballpark as of August 2026 — check before ordering.

### Recommended build — matches the kitchen, ~$100

| Item | ~Price | Notes |
|---|---|---|
| [Raspberry Pi 4 Model B, 2 GB](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/) | $45–55 | 1 GB is fine too. **Not a Pi 5** — it dropped the 3.5 mm jack and needs a beefier PSU for no benefit here. Buy from an [approved reseller](https://www.raspberrypi.com/resellers/) such as [PiShop](https://www.pishop.us/) or [Chicago Electronic Distributors](https://chicagodist.com/products/raspberry-pi-4-model-b-2gb) |
| [Official 5.1 V 3 A USB-C PSU](https://www.adafruit.com/product/4298) | $8–10 | Don't use a random phone charger; undervolting causes weird, hard-to-diagnose faults |
| [SanDisk High Endurance microSD 32 GB](https://www.pishop.us/product/sandisk-high-endurance-microsdhc-card-32gb-blank/) | $12–15 | **Get the High Endurance one.** See the SD card warning below |
| [USB audio adapter](https://www.adafruit.com/product/1475) | $5–10 | Your kitchen uses a C-Media `0d8c:8808` dongle. Any cheap USB one works and beats the Pi's onboard jack |
| Powered speakers with 3.5 mm input | $25–70 | [Creative Pebble 2.0](https://www.amazon.com/Creative-USB-Powered-Speakers-Far-Field-Radiators/dp/B0791H74NT) (~$25, USB-powered, plenty) or [Pebble Pro](https://us.creative.com/p/speakers/creative-pebble-pro) (~$70) to match the kitchen |
| Case | $5–10 | Anything with airflow |

### Budget build — ~$65 all-in

Swap the Pi 4 for a **Pi Zero 2 W** (~$15). Caveats: no analog jack at all (the USB
adapter becomes mandatory), you need a **micro-USB OTG adapter** for it, and 2.4 GHz-only
WiFi makes the big song files download slowly. Workable, fiddlier to assemble.

### The one I'd actually pick if there's ethernet: a used thin client — $30–70

A **Dell Wyse 5070**, **HP t630/t640**, or **Lenovo ThinkCentre Tiny** off eBay runs
Debian, has a line-out jack, and — the real win — **boots from an SSD or eMMC instead
of an SD card**. Search eBay for "Dell Wyse 5070" or "ThinkCentre M900 Tiny". Most have
no WiFi, so plan on ethernet or a USB WiFi dongle. `setup_node.sh` works unchanged on it.

### ⚠️ The SD card is the thing most likely to fail

The audio cache runs pinned at its cap and continuously evicts and re-downloads, which
is nonstop write churn. That's why the High Endurance card matters, and why a thin
client with an SSD is genuinely more reliable. On a small card, shrink the cache:

```bash
sudo ./setup_node.sh --name livingroom --cache-mb 800
```

### Also get: remote access

It's in someone else's house. Without remote access you will drive over for every
hiccup. [Tailscale](https://tailscale.com/) is free for personal use, installs in one
command, and gets you SSH from anywhere:

```bash
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
```

## Install

### 1. Flash the card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/), pick **Raspberry Pi OS
Lite (64-bit)** — no desktop needed. Before writing, open the settings gear and preload:

- hostname (e.g. `soundnode-livingroom`)
- username + password
- **their WiFi SSID and password**
- **enable SSH and paste your public key** (`cat ~/.ssh/id_ed25519.pub`)

Doing this in Imager is what makes the on-site install take ten minutes instead of
needing a keyboard and monitor.

### 2. Boot it, plug in the USB audio adapter, find it

```bash
ping soundnode-livingroom.local
ssh <user>@soundnode-livingroom.local
```

### 3. Copy two files over and run the installer

From this repo on your Mac:

```bash
scp deploy/kitchen_agent.py deploy/setup_node.sh <user>@soundnode-livingroom.local:~/
ssh <user>@soundnode-livingroom.local 'chmod +x setup_node.sh && sudo ./setup_node.sh --name livingroom'
```

Use `--dry-run` first if you want to see what it would do without changing anything.

The script installs `python3-pygame` from apt (pip **cannot** install it — the system
Python is PEP 668 externally-managed), writes a systemd unit, enables it, starts it, and
waits to confirm it came up. It's idempotent, so re-run it to change the name, cache
size, or audio device.

It auto-detects the audio card and pins `hw:CARD=<name>` rather than a card number,
because **card numbers move between reboots** — a stale number crash-loops
`pygame.mixer.init` with "Unknown error 524". It prefers a USB adapter, warns if it only
found the noisy onboard jack, and warns loudly if all it found was HDMI (silent with no
TV attached).

### 4. Verify

```bash
speaker-test -D hw:CARD=Device,DEV=0 -c2 -twav -l1   # do the speakers work at all?
journalctl -u sound-node -f                          # watch it play
```

Then fire something from the board and confirm you hear it. The node also appears in the
server's "who's online" list under the name you gave it.

## Useful commands

```bash
journalctl -u sound-node -f              # live log
journalctl -u sound-node | grep blind    # did polling ever stall?
sudo systemctl restart sound-node
aplay -l                                 # list audio cards
```

## Tuning that node without touching the kitchen

Edit `/etc/systemd/system/sound-node.service`, then
`sudo systemctl daemon-reload && sudo systemctl restart sound-node`:

| Env | Default | What it does |
|---|---|---|
| `SS_NAME` | `kitchen` | Room name in the online list. **Must be unique per node.** |
| `SS_SOUND_GAIN` | `0.6` | Short-clip level for this room |
| `SS_SONG_GAIN` | `0.7` | Song level when nothing else is firing |
| `SS_SONG_DUCK` | `0.6` | Song level while a clip fires |
| `SS_CACHE_CAP_MB` | `2048` | Cache cap |
| `SS_BLIND_WARN` | `2.0` | Log a warning if polling stalls this long |

## Things to know before you commit

- **Every sound plays on every node.** There's no per-room routing — they'll hear
  everything anyone fires, including kitchen-only stuff. Making that selectable is a
  server feature, not config.
- **`box_volume` is global.** One master volume shared across both houses; if they turn
  it down, your kitchen turns down too. The per-node gains above let you trim their room
  *relative* to yours, but the master is shared.
- **No password goes on their box.** Listening only uses the public API, so the unit
  deliberately sets no `SS_PASSWORD`. Anyone on that box can't edit or delete anything.
- **They can fire sounds too**, since the board is open. Usually the point, but be aware.
- **The origin IP is pinned** (`SS_ORIGIN_IP`) to skip Cloudflare, which intermittently
  stalls the agent's polling. If the VPS is ever re-IP'd, every node needs the new value
  — they won't go dead in the meantime, they fall back to normal DNS after ~15 s.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Service active but restarting constantly | Wrong audio device. `aplay -l`, then re-run the installer or fix `SS_AUDIODEV` to `hw:CARD=<name>,DEV=0` |
| Totally silent, no errors in the log | Detected HDMI instead of the DAC, or speakers off/muted. Try `speaker-test` |
| Log shows `⚠ blind for N.Ns` | Polling stalled — network. Check WiFi signal; consider ethernet |
| `download error` lines | Can't reach the server, or its disk is full |
| Node shows as "kitchen" online | `SS_NAME` wasn't set — re-run with `--name` |
| Plays late / stutters on first play of a song | Normal: it's downloading. Only the first play of each track |
