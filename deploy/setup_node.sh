#!/usr/bin/env bash
# Provision a fresh Raspberry Pi OS Lite (or any Debian) box into a sound-server
# playback node: polls the server and plays whatever is firing, on the speakers.
# No buttons, no web UI, no password — listening only needs the public API.
#
#   sudo ./setup_node.sh --name livingroom
#   sudo ./setup_node.sh --name garage --cache-mb 800 --dry-run
#
# Idempotent: safe to re-run to change the name, cache size, or audio device.
set -euo pipefail

NAME=""
CACHE_MB=2048
ORIGIN_IP="149.28.114.237"          # skips Cloudflare; falls back to DNS if wrong
SERVER="https://sounderserver.party"
AUDIODEV=""                          # auto-detected unless given
RUN_USER=""
DRY_RUN=0
SERVICE=sound-node

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --name)      NAME="${2:?}"; shift 2 ;;
    --cache-mb)  CACHE_MB="${2:?}"; shift 2 ;;
    --origin-ip) ORIGIN_IP="${2:-}"; shift 2 ;;
    --server)    SERVER="${2:?}"; shift 2 ;;
    --audiodev)  AUDIODEV="${2:?}"; shift 2 ;;
    --user)      RUN_USER="${2:?}"; shift 2 ;;
    --service)   SERVICE="${2:?}"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done

die() { echo "!! $*" >&2; exit 1; }
say() { echo "==> $*"; }

[ -n "$NAME" ] || die "--name is required (the room, e.g. --name livingroom)"
case "$NAME" in *[!a-zA-Z0-9_-]*) die "--name: use letters, digits, _ or - only";; esac
[ "$DRY_RUN" = 1 ] || [ "$(id -u)" = 0 ] || die "run with sudo (or pass --dry-run)"

# Who owns the service. Default to the invoking sudo user, else the first normal user.
if [ -z "$RUN_USER" ]; then
  RUN_USER="${SUDO_USER:-}"
  [ -n "$RUN_USER" ] || RUN_USER="$(awk -F: '$3>=1000 && $3<65534 {print $1; exit}' /etc/passwd)"
fi
[ -n "$RUN_USER" ] || die "could not determine a user to run as; pass --user"
id "$RUN_USER" >/dev/null 2>&1 || die "no such user: $RUN_USER"
HOME_DIR="$(getent passwd "$RUN_USER" | cut -d: -f6)"
[ -n "$HOME_DIR" ] || die "no home dir for $RUN_USER"

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_SRC="$SRC_DIR/kitchen_agent.py"
[ -f "$AGENT_SRC" ] || die "kitchen_agent.py must sit next to this script (looked in $SRC_DIR)"

# ---------------------------------------------------------------------------
# Audio device detection
# ---------------------------------------------------------------------------
# ALSA card NUMBERS are not stable across reboots — the USB DAC has moved between
# hw:2 and hw:3 on the kitchen Pi, and a stale number crash-loops the agent at
# pygame.mixer.init with "Unknown error 524". So we resolve the card's short NAME
# from `aplay -l` and pin hw:CARD=<name>, which survives reboots.
DETECTED_ID=""
DETECTED_DESC=""
# Sets DETECTED_ID / DETECTED_DESC. Called WITHOUT $(...) on purpose: command
# substitution runs in a subshell, where assignments to these would be lost.
detect_audiodev() {
  DETECTED_ID=""; DETECTED_DESC=""
  command -v aplay >/dev/null 2>&1 || return 0
  local cards pick
  # Lines look like:
  #   card 3: Device [USB Advanced Audio Device], device 0: USB Audio [USB Audio]
  # The card ID is the short token ("Device"); the useful description is in the
  # first [brackets] ("USB Advanced Audio Device"). Match on the DESCRIPTION —
  # the onboard jack's ID is "Headphones" and says bcm2835 only in its description,
  # so filtering on the ID alone silently picks the noisy onboard output.
  cards="$(aplay -l 2>/dev/null |
           sed -n 's/^card [0-9]\+: \([^ ]\+\) \[\([^]]*\)\].*/\1|\2/p' |
           awk '!seen[$0]++')"
  [ -n "$cards" ] || return 0
  # Prefer a USB DAC; then anything that isn't onboard/HDMI; then the Pi's onboard
  # jack (PWM, noticeably noisy); HDMI last since it's silent with no TV attached.
  pick="$(echo "$cards" | grep -i 'usb'                          | head -1 || true)"
  [ -n "$pick" ] || pick="$(echo "$cards" | grep -vEi 'bcm2835|vc4|hdmi' | head -1 || true)"
  [ -n "$pick" ] || pick="$(echo "$cards" | grep -Ei  'bcm2835'          | head -1 || true)"
  [ -n "$pick" ] || pick="$(echo "$cards" | head -1)"
  DETECTED_ID="${pick%%|*}"
  DETECTED_DESC="${pick#*|}"
}

if [ -z "$AUDIODEV" ]; then
  detect_audiodev
  CARD="$DETECTED_ID"
  if [ -n "$CARD" ]; then
    AUDIODEV="hw:CARD=$CARD,DEV=0"
    say "audio: detected '$DETECTED_DESC' (card id $CARD) -> $AUDIODEV"
    case "$DETECTED_DESC" in
      *[Uu][Ss][Bb]*)   echo "    good — that's the USB audio adapter." ;;
      *bcm2835*)        echo "    note: that's the Pi's onboard jack (PWM, a bit noisy)."
                        echo "    A ~\$10 USB audio adapter is a clear upgrade. Plug it in and re-run." ;;
      *hdmi*|*HDMI*)    echo "    WARNING: that's an HDMI output — SILENT unless a TV is attached." ;;
    esac
  else
    AUDIODEV="hw:0,0"
    echo "    WARNING: no ALSA playback cards found. Defaulting to hw:0,0."
    echo "    Plug in the USB audio adapter and re-run this script."
  fi
else
  say "audio: using supplied $AUDIODEV"
fi

UNIT_PATH="/etc/systemd/system/${SERVICE}.service"
UNIT_TEXT="$(cat <<UNIT
[Unit]
Description=Sound node ($NAME) — plays $SERVER on this room's speakers
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
Environment=SS_NAME=$NAME
Environment=SS_SERVER=$SERVER
Environment=SS_AUDIODEV=$AUDIODEV
Environment=SS_CACHE_CAP_MB=$CACHE_MB
$([ -n "$ORIGIN_IP" ] && echo "Environment=SS_ORIGIN_IP=$ORIGIN_IP")
# No SS_PASSWORD on purpose: listening only uses the public API, so a node you
# don't own never needs a credential.
ExecStart=/usr/bin/python3 -u $HOME_DIR/kitchen_agent.py
Restart=always
RestartSec=3
# The agent can sit in a blocking read; don't wait the default 90s to restart it.
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
UNIT
)"

if [ "$DRY_RUN" = 1 ]; then
  say "DRY RUN — nothing will be changed"
  echo "  user:      $RUN_USER ($HOME_DIR)"
  echo "  node name: $NAME"
  echo "  cache cap: ${CACHE_MB}MB"
  echo "  audiodev:  $AUDIODEV"
  echo "  unit:      $UNIT_PATH"
  echo "  agent:     $AGENT_SRC -> $HOME_DIR/kitchen_agent.py"
  echo "--- unit file that would be written ---"
  echo "$UNIT_TEXT"
  exit 0
fi

say "installing packages (python3-pygame from apt — pip can't: PEP668 marks the system Python externally-managed)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pygame alsa-utils

say "installing agent to $HOME_DIR/kitchen_agent.py"
install -o "$RUN_USER" -g "$RUN_USER" -m 0755 "$AGENT_SRC" "$HOME_DIR/kitchen_agent.py"

say "adding $RUN_USER to the audio group"
usermod -aG audio "$RUN_USER" || true

say "writing $UNIT_PATH"
printf '%s\n' "$UNIT_TEXT" > "$UNIT_PATH"

say "enabling + starting ${SERVICE}"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl reset-failed "$SERVICE" 2>/dev/null || true
systemctl restart "$SERVICE"

say "waiting for it to come up ..."
ok=0
for _ in $(seq 1 20); do
  sleep 1
  if journalctl -u "$SERVICE" -n 40 --no-pager 2>/dev/null | grep -q "agent running"; then ok=1; break; fi
  if [ "$(systemctl show "$SERVICE" -p SubState --value)" != "running" ]; then break; fi
done

echo
if [ "$ok" = 1 ]; then
  say "SUCCESS — node '$NAME' is running"
else
  say "started, but did not see the ready line — check the log below"
fi
echo "  restarts: $(systemctl show "$SERVICE" -p NRestarts --value)   state: $(systemctl show "$SERVICE" -p SubState --value)"
echo
journalctl -u "$SERVICE" -n 15 --no-pager 2>/dev/null | grep -viE "runtimewarning|hello from|pygame 2" || true
echo
echo "Useful commands:"
echo "  journalctl -u $SERVICE -f                  # live log"
echo "  journalctl -u $SERVICE | grep blind        # did it ever stall?"
echo "  sudo systemctl restart $SERVICE"
echo "  aplay -l                                   # list audio cards"
echo "  speaker-test -D $AUDIODEV -c2 -twav -l1    # prove the speakers work"
[ "$ok" = 1 ] || exit 1
