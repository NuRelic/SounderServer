# Pi Soundboard (sounderserver.party)

Raspberry Pi 4 soundboard with a web UI, exposed to remote friends via Cloudflare Tunnel.

## Components
- `soundboard_pi.py` — main service (pygame mixer + GPIO buttons + embedded Flask web server on :5000).
  Layered playback (2 songs + unlimited SFX/DCC), session login, presence, chat, activity log,
  scheduled quiet-hours volume.
- `clip_editor.py` — in-browser Clip Studio (yt-dlp/upload → waveform trim → save song/sfx).
- `audio_stream.py` — (legacy) live MP3 broadcaster.
- `normalize_watcher.py` — two-pass loudnorm of new songs in `sounds/sound_1/`.
- `soundboard_watchdog.sh` — soft health watchdog.
- `web/templates/` — `index.html`, `login.html`. `static/` — JS (chat, clip editor, browser audio) + wavesurfer.
- `deploy/` — systemd unit files (reference).

## Not in the repo (see .gitignore)
- `sounds/` — the audio library (~13 GB).
- `.flask_secret` — Flask session signing key (generated on first run).
- Caches, staging, logs, patch backups.

## Run
`soundboard.service`, `normalize.service`, `soundboard-watchdog.service`, `cloudflared.service` (systemd).
Browser audio served as cached 128k MP3 from `cache_audio/`.
