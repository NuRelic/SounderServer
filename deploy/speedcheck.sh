#!/bin/bash
# Periodic download-throughput probe -> ~/speedtest.csv. Ping loss/latency (netmon) does
# NOT capture streaming slowness: a marginal cellular uplink can pass pings at 0% loss
# while delivering poor, variable BANDWIDTH — which is what makes Netflix buffer. This
# measures actual download Mbps so "the internet felt slow" becomes a number.
#
# Data-conscious (the living-room node is metered): one modest download per run, driven
# by speedcheck.timer every ~30 min. Tunable via env:
#   SPEEDCHECK_BYTES  bytes to pull per test (default 5,000,000 = 5 MB; ~240 MB/day at 30-min cadence)
CSV=/home/pi/speedtest.csv
BYTES="${SPEEDCHECK_BYTES:-5000000}"
URL="https://speed.cloudflare.com/__down?bytes=${BYTES}"
[ -f "$CSV" ] || echo "ts,dl_mbps,bytes,secs,http" > "$CSV"

# %{speed_download} is bytes/sec over the transfer; *8/1e6 = Mbps. 25s ceiling so a truly
# dead link doesn't hang or over-spend.
read spd sz tt code < <(curl -s -o /dev/null -m 25 \
  -w "%{speed_download} %{size_download} %{time_total} %{http_code}" "$URL" 2>/dev/null)
mbps=$(awk -v s="${spd:-0}" 'BEGIN{printf "%.1f", s*8/1000000}')
echo "$(date -Is),${mbps},${sz:-0},${tt:-0},${code:-0}" >> "$CSV"

# keep ~2 months of 30-min samples
if [ "$(wc -l < "$CSV")" -gt 3000 ]; then
  { head -1 "$CSV"; tail -2800 "$CSV" | grep -v '^ts,'; } > "$CSV.tmp" && mv "$CSV.tmp" "$CSV"
fi
