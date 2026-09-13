#!/bin/bash
# Query the node's Inseego FX4100 gateway over ubus. Read-only by default.
#
#   gw.sh <namespace> <method> [json-args]   # raw call, pretty-printed
#   gw.sh signal                             # the numbers that matter, one line
#   gw.sh 5g                                 # full 5G NR service stats
#
# Needs /etc/soundnode-gw.conf (GW_URL, GW_PW) — same file netmon/devmon use.
set -u
CONF=/etc/soundnode-gw.conf
[ -f "$CONF" ] || { echo "no $CONF" >&2; exit 1; }
. "$CONF"
Z=00000000000000000000000000000000

rpc(){ curl -sk -m 10 -A "Mozilla/5.0" -H "Content-Type: application/json" \
            -H "Referer: $GW_URL/" -d "$1" "$GW_URL/ubus" 2>/dev/null; }
login(){ rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\",\"params\":[\"$Z\",\"webui.login\",\"authenticate\",{\"password\":\"$GW_PW\"}]}" \
           | grep -oE '"session_token":"[a-f0-9]+"' | cut -d'"' -f4; }
call(){ local a="${4-}"; [ -n "$a" ] || a="{}"; rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\",\"params\":[\"$1\",\"$2\",\"$3\",$a]}"; }
pretty(){ python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)["result"]
    print(json.dumps(d[1] if len(d)>1 else d[0],indent=2,sort_keys=True))
except Exception: sys.stdout.write("(unparseable) ")' 2>/dev/null; }

TOK=$(login); [ -n "$TOK" ] || { echo "gateway login failed" >&2; exit 1; }

case "${1:-signal}" in
  signal)
    c=$(call "$TOK" sysinterface.modem get_cellular_service_stats)
    u=$(call "$TOK" sysinterface.ui get_llp_device_info)
    g(){ echo "$1" | grep -oE "\"$2\":-?[0-9]+" | grep -oE -- '-?[0-9]+$'; }
    s(){ echo "$1" | grep -oE "\"$2\":\"[^\"]*\"" | cut -d'"' -f4; }
    echo "rsrp=$(g "$c" rsrp)dBm rsrq=$(g "$c" rsrq)dB snr=$(g "$c" snr)dB pci=$(g "$c" pci) cell=$(s "$c" cell_id) band=$(s "$u" band) bw=$(s "$u" bandwidth) bars=$(g "$c" bar)"
    ;;
  5g)   call "$TOK" sysinterface.modem get_cellular_5g_service_stats | pretty; echo ;;
  paths)
    # rsrp2 (the second receive path) is only exposed in the diagnostic text blob.
    # Divergence between rsrp and rsrp2 means one antenna path is dead/unconnected.
    d=$(call "$TOK" sysinterface.modem get_diagnostic_data)
    a=$(echo "$d" | grep -oE 'rsrp:\[-?[0-9]+\]' | head -1 | sed 's/.*\[//; s/\]//')
    b=$(echo "$d" | grep -oE 'rsrp2:\[-?[0-9]+\]' | head -1 | sed 's/.*\[//; s/\]//')
    echo "rsrp=${a:-NA} rsrp2=${b:-NA} delta=$(( ${b:-0} - ${a:-0} ))dB"
    ;;
  diag) call "$TOK" sysinterface.modem get_diagnostic_data | pretty; echo ;;
  *)    [ $# -ge 2 ] || { echo "usage: gw.sh <namespace> <method> [json]" >&2; exit 1; }
        call "$TOK" "$1" "$2" "${3-}" | pretty; echo ;;
esac
