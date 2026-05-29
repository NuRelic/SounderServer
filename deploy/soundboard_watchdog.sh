#!/bin/bash
# Soft watchdog for the Pi soundboard.
# Polls the in-process Flask health endpoint. On 3 consecutive failures it
# restarts soundboard.service; if still unhealthy after the restart, it reboots.
# Hardware-level USB-audio/kernel hangs are caught separately by the systemd
# RuntimeWatchdogSec hardware watchdog.

URL="http://localhost:5000/api/status"
THRESHOLD=3
INTERVAL=30
fails=0

is_healthy() {
    # Flask is "up" if it returns ANY HTTP status. 200 = normal; 401 = up but
    # gated by Basic Auth (which is fine - the server is clearly alive). A
    # connection refusal / timeout yields code 000 = down. This deliberately
    # does NOT send credentials, so it keeps working if the password changes.
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$URL")
    [ "$code" = "200" ] || [ "$code" = "401" ]
}

while true; do
    if is_healthy; then
        fails=0
    else
        fails=$((fails + 1))
        logger -t soundboard-watchdog "health check failed (${fails}/${THRESHOLD})"
        if [ "$fails" -ge "$THRESHOLD" ]; then
            logger -t soundboard-watchdog "restarting soundboard.service after ${fails} failures"
            systemctl restart soundboard.service
            sleep 20
            if ! is_healthy; then
                logger -t soundboard-watchdog "still unhealthy after restart; rebooting"
                systemctl reboot
            fi
            fails=0
        fi
    fi
    sleep "$INTERVAL"
done
