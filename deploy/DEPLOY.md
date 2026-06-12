# Deploying the Sound Server to the Vultr box

Assumes Ubuntu 24.04, you SSH in as `root` first. Replace `<VPS_IP>` and paths as needed.
Goal: app on `127.0.0.1:5000`, Caddy fronts it with HTTPS at `new.sounderserver.party`.

## 1. Base system + a non-root user
```
adduser --disabled-password --gecos "" sound
usermod -aG sudo sound
mkdir -p /home/sound/.ssh && cp ~/.ssh/authorized_keys /home/sound/.ssh/ \
  && chown -R sound:sound /home/sound/.ssh && chmod 700 /home/sound/.ssh

ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
apt update && apt -y upgrade
apt -y install python3-venv python3-pip ffmpeg git rsync caddy
curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -o /usr/local/bin/yt-dlp && chmod a+rx /usr/local/bin/yt-dlp
```

## 2. Get the app onto the box
Option A (rsync from your Mac — run THIS on the Mac):
```
rsync -av --exclude data --exclude .venv \
  ~/sound-server-staging/ sound@<VPS_IP>:/home/sound/sound-server/
```
Then back on the VPS as `sound`:
```
cd /home/sound/sound-server
python3 -m venv .venv && ./.venv/bin/pip install -U pip flask
mkdir -p data
```

## 3. Upload the library + the catalog DB (run on the Mac)
```
rsync -av ~/Downloads/Sounds/  sound@<VPS_IP>:/home/sound/sounds/
rsync -av ~/Downloads/sounds.db sound@<VPS_IP>:/home/sound/sounds.db
```
(~2 GB — mostly waiting. The NSFW review folder stays on your Mac, not uploaded.)

## 4. Secrets / env + service (VPS, as root)
```
cp /home/sound/sound-server/deploy/soundserver.env.example /etc/soundserver.env
# edit /etc/soundserver.env — set/confirm USER_PASS, ADMIN_PASS
chmod 600 /etc/soundserver.env

cp /home/sound/sound-server/deploy/soundserver.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now soundserver
systemctl status soundserver        # should be active; curl 127.0.0.1:5000 works
```

## 5. DNS + HTTPS
1. Cloudflare → add an **A record** `new` → `<VPS_IP>`, **DNS only (grey cloud)** for now.
2. Put the Caddyfile in place and reload:
   ```
   cp /home/sound/sound-server/deploy/Caddyfile /etc/caddy/Caddyfile
   systemctl reload caddy
   ```
3. Visit `https://new.sounderserver.party` — Caddy gets a cert; you should hit the access wall.
4. Once it works, flip the Cloudflare record to **Proxied (orange)** and set SSL/TLS mode to **Full (strict)** for edge caching/DDoS.

## 6. Cut the apex over (when happy)
Point `sounderserver.party` at the VPS (replace the old Pi tunnel record), add the apex block to the Caddyfile, reload. The Pi stays as a fallback.

## Updating later
Re-run the step-2 rsync from the Mac, then `systemctl restart soundserver` on the VPS.
(`data/`, `.venv`, and the NSFW review folder are excluded from rsync, so server-side state/favorites/catalog are preserved.)
