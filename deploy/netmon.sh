#!/bin/bash
# One sample of this node's network health -> one CSV row every run (driven by
# netmon.timer, ~5 min). Cheap on purpose so it can run forever. Captures the exact
# signals that have bitten the living-room node: WiFi channel drift (freq), internet
# latency/loss, and — when a gateway config is present — the 5G uplink signal (RSRP/
# RSRQ/SINR) and the gateway's pinned 5G channel.
#
# Gateway querying is OPTIONAL: only runs if /etc/soundnode-gw.conf exists, which holds
#   GW_URL=https://192.168.1.1
#   GW_PW='<admin password>'
# (chmod 600, owned by the user this runs as). No config -> the gateway columns are blank
# and only the local WiFi/latency data is logged. Keeps this script generic per node.
CSV=/home/pi/netmon.csv
HEADER="ts,rssi_dbm,linkspeed_mbps,freq_mhz,chan,gw_avg_ms,gw_max_ms,gw_loss_pct,net_avg_ms,net_max_ms,net_loss_pct,rsrp_dbm,rsrq_db,sinr_db,gw_5g_chan,dev_count,wifi_state"

# Schema rotation: if the existing file's header differs (older/newer schema), archive it
# so the CSV is always internally consistent for whoever parses it.
if [ -f "$CSV" ]; then
  if [ "$(head -1 "$CSV" 2>/dev/null)" != "$HEADER" ]; then
    mv "$CSV" "$CSV.$(head -1 "$CSV" | wc -c)cols.old" 2>/dev/null
  fi
fi
[ -f "$CSV" ] || echo "$HEADER" > "$CSV"

# ---- local WiFi + latency ----
POLL=$(wpa_cli -i wlan0 signal_poll 2>/dev/null)
RSSI=$(echo "$POLL" | sed -n 's/^RSSI=//p')
LSPD=$(echo "$POLL" | sed -n 's/^LINKSPEED=//p')
FREQ=$(echo "$POLL" | sed -n 's/^FREQUENCY=//p')
# WiFi channel from frequency: 5 GHz -> (f-5000)/5 ; 2.4 GHz -> (f-2407)/5
CHAN=""
if [ -n "$FREQ" ]; then
  if [ "$FREQ" -ge 5000 ]; then CHAN=$(( (FREQ-5000)/5 )); else CHAN=$(( (FREQ-2407)/5 )); fi
fi
STATE=$(nmcli -t -f DEVICE,STATE device 2>/dev/null | sed -n 's/^wlan0://p')

GW=$(ip route show default | awk '{print $3; exit}')
g=$(ping -c 10 -i 0.2 -W 2 -q "$GW" 2>/dev/null | tail -3)
gloss=$(echo "$g" | sed -n 's/.*, \([0-9.]*\)% packet loss.*/\1/p')
gavg=$(echo  "$g" | awk -F'/' '/rtt|round-trip/{print $5}')
gmax=$(echo  "$g" | awk -F'/' '/rtt|round-trip/{print $6}')
n=$(ping -c 10 -i 0.2 -W 2 -q 1.1.1.1 2>/dev/null | tail -3)
nloss=$(echo "$n" | sed -n 's/.*, \([0-9.]*\)% packet loss.*/\1/p')
navg=$(echo  "$n" | awk -F'/' '/rtt|round-trip/{print $5}')
nmax=$(echo  "$n" | awk -F'/' '/rtt|round-trip/{print $6}')

# ---- optional gateway signal (5G uplink + pinned channel + device count) ----
RSRP=""; RSRQ=""; SINR=""; GW5G=""; DEVN=""
if [ -f /etc/soundnode-gw.conf ]; then
  . /etc/soundnode-gw.conf
  UA="Mozilla/5.0"; Z=00000000000000000000000000000000
  rpc(){ curl -sk -m 8 -A "$UA" -H "Content-Type: application/json" -H "Referer: $GW_URL/" -d "$1" "$GW_URL/ubus" 2>/dev/null; }
  TOK=$(rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\",\"params\":[\"$Z\",\"webui.login\",\"authenticate\",{\"password\":\"$GW_PW\"}]}" | grep -oE '"session_token":"[a-f0-9]+"' | cut -d'"' -f4)
  if [ -n "$TOK" ]; then
    cell=$(rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\",\"params\":[\"$TOK\",\"sysinterface.modem\",\"get_cellular_service_stats\",{}]}")
    RSRP=$(echo "$cell" | grep -oE '"rsrp":-?[0-9]+' | grep -oE -- '-?[0-9]+$')
    RSRQ=$(echo "$cell" | grep -oE '"rsrq":-?[0-9]+' | grep -oE -- '-?[0-9]+$')
    SINR=$(echo "$cell" | grep -oE '"sinr":-?[0-9]+' | grep -oE -- '-?[0-9]+$')
    GW5G=$(rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\",\"params\":[\"$TOK\",\"sysinterface.wifi\",\"get_advanced_settings\",{}]}" | grep -oE '"wifi_5g_channel":[0-9]+' | grep -oE '[0-9]+$')
    DEVN=$(rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"call\",\"params\":[\"$TOK\",\"devui\",\"get_connected_devices\",{}]}" | grep -oc '"mac"')
  fi
fi

echo "$(date -Is),${RSSI},${LSPD},${FREQ},${CHAN},${gavg},${gmax},${gloss:-100},${navg},${nmax},${nloss:-100},${RSRP},${RSRQ},${SINR},${GW5G},${DEVN},${STATE:-unknown}" >> "$CSV"

# keep ~2 months of 5-min samples, then trim oldest (preserve header)
if [ "$(wc -l < "$CSV")" -gt 18000 ]; then
  { head -1 "$CSV"; tail -17000 "$CSV" | grep -v "^ts,"; } > "$CSV.tmp" && mv "$CSV.tmp" "$CSV"
fi
