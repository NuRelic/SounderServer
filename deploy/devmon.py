#!/usr/bin/env python3
"""Per-device connectivity sampler for a node's gateway (Inseego FX4100 via ubus).

Run every ~2 min by devmon.timer. Each run asks the gateway which devices are currently
connected, then:
  - appends one row per connected device to  ~/devmon.csv   (presence + signal history)
  - diffs against the previous sample and appends human-readable JOIN/DROP lines to
    ~/devdrops.log  — the "diary" that shows exactly when a device (e.g. the Xbox)
    fell off and came back, so intermittent-drop patterns are visible over a day.

Needs /etc/soundnode-gw.conf with GW_URL and GW_PW (same file netmon uses). Best-effort:
any error is logged to devdrops.log and the run exits without disturbing anything.
"""
import os, json, ssl, time, urllib.request

HOME   = os.path.expanduser("~")
CSV    = os.path.join(HOME, "devmon.csv")
DROPS  = os.path.join(HOME, "devdrops.log")
STATE  = os.path.join(HOME, ".devmon_state.json")
CONF   = "/etc/soundnode-gw.conf"
HEADER = "ts,mac,name,ip,iface_type,signal_dbm,bars\n"

def _conf():
    d = {}
    with open(CONF) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip().strip("'").strip('"')
    return d

def _ubus(url, body):
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url + "/ubus", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "Mozilla/5.0", "Referer": url + "/"})
    return json.loads(urllib.request.urlopen(req, timeout=10, context=ctx).read())

def _log_drop(line):
    with open(DROPS, "a") as f:
        f.write(line + "\n")

def main():
    try:
        c = _conf(); url = c["GW_URL"]; pw = c["GW_PW"]
    except Exception as e:
        return
    Z = "0" * 32
    try:
        tok = _ubus(url, {"jsonrpc": "2.0", "id": 1, "method": "call",
                          "params": [Z, "webui.login", "authenticate", {"password": pw}]})["result"][1]["session_token"]
        res = _ubus(url, {"jsonrpc": "2.0", "id": 1, "method": "call",
                          "params": [tok, "sysinterface.router.device", "get_connected_devices", {}]})["result"]
        devs = (res[1] if len(res) > 1 else {}).get("list", []) if isinstance(res[1] if len(res) > 1 else None, dict) else []
    except Exception as e:
        _log_drop("%s  ERROR querying gateway: %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), e))
        return

    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    now = {}
    rows = []
    for d in devs:
        mac = (d.get("mac_address") or "").lower()
        if not mac:
            continue
        name = d.get("device_name") or d.get("host_name") or mac
        now[mac] = name
        rows.append("%s,%s,%s,%s,%s,%s,%s" % (
            ts, mac, name.replace(",", " "), d.get("ip_address", ""),
            d.get("interface_type", ""), d.get("signal_strength", ""), d.get("bars", "")))

    # presence CSV
    new = not os.path.exists(CSV)
    with open(CSV, "a") as f:
        if new: f.write(HEADER)
        for r in rows: f.write(r + "\n")

    # diff vs previous sample -> join/drop diary
    prev = {}
    try:
        prev = json.load(open(STATE)).get("devices", {})
    except Exception:
        prev = {}
    if prev:                                  # skip the very first run (everything would look "joined")
        for mac, name in now.items():
            if mac not in prev:
                _log_drop("%s  JOIN  %-24s %s" % (ts, name, mac))
        for mac, name in prev.items():
            if mac not in now:
                _log_drop("%s  DROP  %-24s %s" % (ts, name, mac))
    json.dump({"ts": ts, "devices": now}, open(STATE, "w"))

    # trim logs (~2 months of samples; ~keep drops log bounded too)
    for path, cap in ((CSV, 60000), (DROPS, 5000)):
        try:
            lines = open(path).read().splitlines()
            if len(lines) > cap:
                head = [lines[0]] if path == CSV else []
                open(path, "w").write("\n".join(head + lines[-(cap - len(head)):]) + "\n")
        except Exception:
            pass

if __name__ == "__main__":
    main()
