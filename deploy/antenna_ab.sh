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

sample(){ # echoes "rsrp rsrp2 snr pci loss"
  local tok cell loss
  tok=$(login)
  if [ -z "$tok" ]; then echo "NA NA NA NA 100"; return; fi
  cell=$(call "$tok" "sysinterface.modem" "get_cellular_service_stats" "{}")
  # Second receive path: only in the diagnostic blob. If rsrp2 collapses away from
  # rsrp when the external antenna is on, only one connector is attached.
  diag=$(call "$tok" "sysinterface.modem" "get_diagnostic_data" "{}")
  rsrp2=$(echo "$diag" | grep -oE 'rsrp2:\[-?[0-9]+\]' | head -1 | sed 's/.*\[//; s/\]//')
  loss=$(ping -c 5 -i 0.2 -W 2 -q 1.1.1.1 2>/dev/null | sed -n 's/.*, \([0-9.]*\)% packet loss.*/\1/p')
  echo "$(echo "$cell" | grep -oE '"rsrp":-?[0-9]+' | grep -oE -- '-?[0-9]+$') \
${rsrp2:-NA} \
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


aim(){ # $1=seconds. Live readout for physically aiming a directional panel.
  local secs="${1:-600}" tok="" cell diag rsrp rsrp2 snr best=-999 t0 el
  log "=== AIM mode: ${secs}s. Antenna ENABLED; auto-reverts unless '$0 keep' ==="
  rm -f "$KEEP"
  setsid nohup "$0" __watchdog "$secs" >/dev/null 2>&1 &
  echo $! > "$WDPID"
  log "WATCHDOG ARMED (pid $(cat $WDPID))"
  log "ENABLING external antenna: $(set_ext 1)"
  sleep 40
  t0=$(date +%s)
  while :; do
    el=$(( $(date +%s) - t0 ))
    [ "$el" -ge "$secs" ] && break
    [ -n "$tok" ] || tok=$(login)
    cell=$(call "$tok" "sysinterface.modem" "get_cellular_service_stats" "{}")
    rsrp=$(echo "$cell" | grep -oE '"rsrp":-?[0-9]+' | grep -oE -- '-?[0-9]+$')
    if [ -z "$rsrp" ]; then tok=$(login); sleep 1; continue; fi
    snr=$(echo "$cell" | grep -oE '"snr":-?[0-9]+' | grep -oE -- '-?[0-9]+$')
    diag=$(call "$tok" "sysinterface.modem" "get_diagnostic_data" "{}")
    rsrp2=$(echo "$diag" | grep -oE 'rsrp2:\[-?[0-9]+\]' | head -1 | sed 's/.*\[//; s/\]//')
    [ "$rsrp" -gt "$best" ] 2>/dev/null && best="$rsrp"
    printf "%3ds  rsrp=%-5s rsrp2=%-5s snr=%-4s  BEST=%s  %s\n" \
      "$el" "$rsrp" "${rsrp2:-NA}" "${snr:-NA}" "$best" \
      "$(awk -v r="$rsrp" 'BEGIN{n=int((r+120)/2); if(n<0)n=0; if(n>28)n=28; s=""; while(n-->0) s=s"#"; print s}')"
    sleep 3
  done
  log "=== AIM window over. best rsrp=$best. Run '$0 keep' to hold, else it reverts. ==="
}

POSCSV=/home/pi/antenna_positions.csv

begin(){ # $1=session seconds. Enable the antenna once and hold it for the whole aiming session.
  local secs="${1:-3600}"
  rm -f "$KEEP"
  if [ -f "$WDPID" ] && kill -0 "$(cat $WDPID)" 2>/dev/null; then kill "$(cat $WDPID)" 2>/dev/null; fi
  log "=== AIM SESSION: antenna enabled for up to ${secs}s ==="
  setsid nohup "$0" __watchdog "$secs" >/dev/null 2>&1 &
  echo $! > "$WDPID"
  log "WATCHDOG ARMED (pid $(cat $WDPID)) - reverts on 3 straight internet failures, or at ${secs}s"
  log "ENABLING external antenna: $(set_ext 1)"
  log "waiting 40s for the modem to re-register..."
  sleep 40
  log "Ready. Mark each position with: $0 mark <N>"
}

mark(){ # $1=position number  $2=seconds of sampling (default 30)
  local n="$1" secs="${2:-30}" t0 el tok="" cell diag r r2 sn ls tmp best
  [ -n "$n" ] || { echo "usage: $0 mark <position#> [secs]"; return 1; }
  # Guard: a mark taken with the external antenna disabled measures the INTERNAL
  # antennas and is silently meaningless. The session watchdog can revert mid-run,
  # so verify state on every mark rather than trusting that begin() is still in force.
  local en
  en=$(call "$(login)" "sysinterface.modem.antenna" "$GET_METHOD" "{}" | grep -oE '"enabled":[0-9]+' | grep -oE '[0-9]+$')
  if [ "${en:-0}" != "1" ]; then
    echo "REFUSING: external antenna is DISABLED (ext_mhb=${en:-?})." >&2
    echo "The session watchdog has probably expired. Re-arm with: $0 begin <secs>" >&2
    return 1
  fi
  [ -f "$POSCSV" ] || echo "ts,pos,samples,rsrp_med,rsrp2_med,snr_med,loss_mean,rsrp_min,rsrp_max" > "$POSCSV"
  tmp=$(mktemp)
  echo "--- position $n: sampling ${secs}s, hold the antenna STILL and stand clear ---"
  t0=$(date +%s)
  while :; do
    el=$(( $(date +%s) - t0 )); [ "$el" -ge "$secs" ] && break
    [ -n "$tok" ] || tok=$(login)
    cell=$(call "$tok" "sysinterface.modem" "get_cellular_service_stats" "{}")
    r=$(echo "$cell" | grep -oE '"rsrp":-?[0-9]+' | grep -oE -- '-?[0-9]+$')
    if [ -z "$r" ]; then tok=$(login); sleep 1; continue; fi
    sn=$(echo "$cell" | grep -oE '"snr":-?[0-9]+' | grep -oE -- '-?[0-9]+$')
    diag=$(call "$tok" "sysinterface.modem" "get_diagnostic_data" "{}")
    r2=$(echo "$diag" | grep -oE 'rsrp2:\[-?[0-9]+\]' | head -1 | sed 's/.*\[//; s/\]//')
    ls=$(ping -c 4 -i 0.2 -W 2 -q 1.1.1.1 2>/dev/null | sed -n 's/.*, \([0-9.]*\)% packet loss.*/\1/p')
    echo "$r ${r2:-0} ${sn:-0} ${ls:-100}" >> "$tmp"
    printf "  %2ds  rsrp=%-5s rsrp2=%-5s snr=%-4s loss=%s%%\n" "$el" "$r" "${r2:-NA}" "${sn:-NA}" "${ls:-?}"
    sleep 3
  done
  awk -v n="$n" -v csv="$POSCSV" '
    { r[NR]=$1; r2[NR]=$2; s[NR]=$3; l+=$4; c++ }
    END{
      if(c==0){ print "no samples"; exit 1 }
      asort(r); asort(r2); asort(s)
      m=int((c+1)/2)
      printf "\n== position %s: rsrp=%d  rsrp2=%d  snr=%d  loss=%.1f%%  (n=%d, rsrp %d..%d)\n",
             n, r[m], r2[m], s[m], l/c, c, r[1], r[c]
      printf "%s,%s,%d,%d,%d,%d,%.1f,%d,%d\n",
             strftime("%Y-%m-%dT%H:%M:%S"), n, c, r[m], r2[m], s[m], l/c, r[1], r[c] >> csv
    }' "$tmp" 2>/dev/null || awk -v n="$n" -v csv="$POSCSV" '
    { r[NR]=$1; r2[NR]=$2; s[NR]=$3; l+=$4; c++ }
    END{
      if(c==0){ print "no samples"; exit 1 }
      # no asort (mawk): use mean instead of median
      for(i=1;i<=c;i++){ sr+=r[i]; sr2+=r2[i]; ss+=s[i]; if(r[i]<mn||mn==0)mn=r[i]; if(r[i]>mx||mx==0)mx=r[i] }
      printf "\n== position %s: rsrp=%.1f  rsrp2=%.1f  snr=%.1f  loss=%.1f%%  (n=%d, rsrp %d..%d)\n",
             n, sr/c, sr2/c, ss/c, l/c, c, mn, mx
      printf "%s,%s,%d,%.1f,%.1f,%.1f,%.1f,%d,%d\n",
             strftime("%Y-%m-%dT%H:%M:%S"), n, c, sr/c, sr2/c, ss/c, l/c, mn, mx >> csv
    }' "$tmp"
  rm -f "$tmp"
  echo
  table
}

table(){
  [ -f "$POSCSV" ] || { echo "(no positions marked yet)"; return; }
  echo "=== all positions so far (best rsrp last) ==="
  { head -1 "$POSCSV"; tail -n +2 "$POSCSV" | sort -t, -k4 -n; } | column -s, -t
}

case "${1:-status}" in
  __watchdog) watchdog "$2" ;;
  aim) aim "${2:-600}" ;;
  begin) begin "${2:-3600}" ;;
  mark) mark "${2:-}" "${3:-30}" ;;
  table) table ;;

  status)
    tok=$(login)
    echo "ext_antenna    : $(call "$tok" sysinterface.modem.antenna get_ext_antenna_enabled '{}')"
    echo "ext_mhb        : $(call "$tok" sysinterface.modem.antenna "$GET_METHOD" '{}')"
    echo "antenna state  : $(call "$tok" sysinterface.modem.antenna get_state '{}')"
    echo "sample (rsrp rsrp2 snr pci loss): $(sample)"
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
    for i in 1 2 3; do log "  base[$i] rsrp rsrp2 snr pci loss: $(sample)"; sleep 5; done

    # Arm the revert BEFORE making the change. Detached so it outlives this shell,
    # this SSH session, and any WAN outage.
    setsid nohup "$0" __watchdog "$HOLD" >/dev/null 2>&1 &
    echo $! > "$WDPID"
    log "WATCHDOG ARMED (pid $(cat $WDPID)): auto-revert in ${HOLD}s unless '$0 keep'"

    log "ENABLING external antenna: $(set_ext 1)"
    log "waiting 45s for the modem to re-register..."
    sleep 45
    log "AFTER (external antenna):"
    for i in 1 2 3; do log "  after[$i] rsrp rsrp2 snr pci loss: $(sample)"; sleep 5; done
    log "Toggle now reads: $(tok=$(login); call "$tok" sysinterface.modem.antenna "$GET_METHOD" '{}')"
    log "=== Decide within ${HOLD}s: '$0 keep' to keep, else it auto-reverts ==="
    ;;
  *) echo "usage: $0 {begin [secs]|mark <N> [secs]|table|run [secs]|aim [secs]|keep|revert|status}"; exit 1 ;;
esac
