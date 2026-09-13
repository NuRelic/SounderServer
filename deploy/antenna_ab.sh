#!/bin/bash
# A/B-test the FX4100 external antenna with a LAN-local dead-man's switch.
#
# WHY THE WATCHDOG: this node is reachable only over Tailscale *through this gateway*.
# If enabling the external antenna kills the cellular link, the operator loses the path
# to undo it. So the revert is armed BEFORE the change and runs detached on this Pi,
# talking to the gateway over the LAN (192.168.1.x) — a path that does not touch the
# internet and therefore survives a total WAN outage.
#
# Default outcome is ALWAYS revert. Keeping the change requires an explicit
# `antenna_ab.sh keep`, which is the only thing that disarms the watchdog.
#
# Usage:  antenna_ab.sh run [hold_seconds]   # baseline, enable, measure, auto-revert
#         antenna_ab.sh keep                 # disarm watchdog, keep antenna enabled
#         antenna_ab.sh revert               # revert right now
#         antenna_ab.sh status               # current toggles + signal
set -u

CONF=/etc/soundnode-gw.conf
KEEP=/home/pi/.antenna_ab.keep
WDPID=/home/pi/.antenna_ab.wd.pid
LOG=/home/pi/antenna_ab.log
BAND_METHOD=set_ext_mhb_antenna_enabled   # n41 = mid-band. Confirmed via band:'n41'.
GET_METHOD=get_ext_mhb_antenna_enabled
Z=00000000000000000000000000000000

[ -f "$CONF" ] || { echo "no $CONF"; exit 1; }
. "$CONF"

log(){ echo "$(date -Is) $*" | tee -a "$LOG"; }

rpc(){ # $1=body
  curl -sk -m 10 -A "Mozilla/5.0" -H "Content-Type: application/json" \
       -H "Referer: $GW_URL/" -d "$1" "$GW_URL/ubus" 2>/dev/null
}
login(){
  rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\",\"params\":[\"$Z\",\"webui.login\",\"authenticate\",{\"password\":\"$GW_PW\"}]}" \
    | grep -oE '"session_token":"[a-f0-9]+"' | cut -d'"' -f4
}
call(){ # $1=token $2=namespace $3=method $4=args-json
  rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\",\"params\":[\"$1\",\"$2\",\"$3\",$4]}"
}

set_ext(){ # $1=0|1  -> echoes raw response
  local tok; tok=$(login)
  [ -n "$tok" ] || { echo "LOGIN_FAILED"; return 1; }
  call "$tok" "sysinterface.modem.antenna" "$BAND_METHOD" "{\"enabled\":$1}"
}

sample(){ # echoes "rsrp snr pci loss"
  local tok cell loss
  tok=$(login)
  if [ -z "$tok" ]; then echo "NA NA NA 100"; return; fi
  cell=$(call "$tok" "sysinterface.modem" "get_cellular_service_stats" "{}")
  loss=$(ping -c 5 -i 0.2 -W 2 -q 1.1.1.1 2>/dev/null | sed -n 's/.*, \([0-9.]*\)% packet loss.*/\1/p')
  echo "$(echo "$cell" | grep -oE '"rsrp":-?[0-9]+' | grep -oE -- '-?[0-9]+$') \
$(echo "$cell" | grep -oE '"snr":-?[0-9]+' | grep -oE -- '-?[0-9]+$') \
$(echo "$cell" | grep -oE '"pci":[0-9]+' | grep -oE '[0-9]+$') \
${loss:-100}" | tr -s ' '
}

watchdog(){ # $1=hold seconds. Detached. Reverts unless KEEP appears.
  local hold="$1" waited=0 fails=0
  while [ "$waited" -lt "$hold" ]; do
    sleep 15; waited=$((waited+15))
    [ -f "$KEEP" ] && { log "WATCHDOG: keep flag set, standing down (antenna left ENABLED)"; exit 0; }
    # Early bail-out: three consecutive internet failures means the change hurt.
    if ping -c 3 -W 2 -q 1.1.1.1 >/dev/null 2>&1; then fails=0; else
      fails=$((fails+1))
      log "WATCHDOG: internet check failed ($fails/3) at ${waited}s"
      if [ "$fails" -ge 3 ]; then
        log "WATCHDOG: link down -> reverting NOW"
        log "WATCHDOG: revert response: $(set_ext 0)"
        exit 0
      fi
    fi
  done
  [ -f "$KEEP" ] && { log "WATCHDOG: keep flag set, standing down"; exit 0; }
  log "WATCHDOG: hold expired with no keep -> reverting"
  log "WATCHDOG: revert response: $(set_ext 0)"
}

case "${1:-status}" in
  __watchdog) watchdog "$2" ;;

  status)
    tok=$(login)
    echo "ext_antenna    : $(call "$tok" sysinterface.modem.antenna get_ext_antenna_enabled '{}')"
    echo "ext_mhb        : $(call "$tok" sysinterface.modem.antenna "$GET_METHOD" '{}')"
    echo "antenna state  : $(call "$tok" sysinterface.modem.antenna get_state '{}')"
    echo "sample (rsrp snr pci loss): $(sample)"
    [ -f "$KEEP" ] && echo "keep flag: SET" || echo "keep flag: not set"
    if [ -f "$WDPID" ] && kill -0 "$(cat $WDPID)" 2>/dev/null; then
      echo "watchdog: RUNNING (pid $(cat $WDPID))"
    else
      echo "watchdog: not running"
    fi
    ;;

  keep)
    touch "$KEEP"
    log "KEEP set by operator — antenna stays enabled, watchdog will stand down."
    ;;

  revert)
    log "Manual revert requested: $(set_ext 0)"
    ;;

  run)
    HOLD="${2:-300}"
    rm -f "$KEEP"
    log "=== A/B run: hold=${HOLD}s, method=$BAND_METHOD ==="
    log "BASELINE (internal antenna):"
    for i in 1 2 3; do log "  base[$i] rsrp snr pci loss: $(sample)"; sleep 5; done

    # Arm the revert BEFORE making the change. Detached so it outlives this shell,
    # this SSH session, and any WAN outage.
    setsid nohup "$0" __watchdog "$HOLD" >/dev/null 2>&1 &
    echo $! > "$WDPID"
    log "WATCHDOG ARMED (pid $(cat $WDPID)): auto-revert in ${HOLD}s unless '$0 keep'"

    log "ENABLING external antenna: $(set_ext 1)"
    log "waiting 45s for the modem to re-register..."
    sleep 45
    log "AFTER (external antenna):"
    for i in 1 2 3; do log "  after[$i] rsrp snr pci loss: $(sample)"; sleep 5; done
    log "Toggle now reads: $(tok=$(login); call "$tok" sysinterface.modem.antenna "$GET_METHOD" '{}')"
    log "=== Decide within ${HOLD}s: '$0 keep' to keep, else it auto-reverts ==="
    ;;
  *) echo "usage: $0 {run [secs]|keep|revert|status}"; exit 1 ;;
esac
