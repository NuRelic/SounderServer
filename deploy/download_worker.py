#!/usr/bin/env python3
"""
Sound Server download worker — runs on the Pi (residential IP, not YouTube
bot-gated). Polls the VPS for queued URL-download jobs, runs yt-dlp locally,
and uploads the finished audio back to the VPS. Only makes OUTBOUND requests,
so it needs no inbound ports / tunnel.

Env:
  SS_SERVER        VPS base URL (default https://sounderserver.party)
  SS_WORKER_TOKEN  shared secret (matches the VPS data/worker_token)
  SS_YTDLP         path to yt-dlp (default: yt-dlp on PATH)
"""
import os, time, glob, shutil, tempfile, subprocess
import requests

SERVER = os.environ.get("SS_SERVER", "https://sounderserver.party").rstrip("/")
TOKEN  = os.environ.get("SS_WORKER_TOKEN", "").strip()
YTDLP  = os.environ.get("SS_YTDLP") or shutil.which("yt-dlp") or "/usr/local/bin/yt-dlp"
DENO   = os.environ.get("SS_DENO", "/usr/local/bin/deno64")   # JS runtime wrapper (full YT extraction)
HEAD   = {"X-Worker-Token": TOKEN}
POLL_IDLE = 3.0     # seconds between polls when there's no work

def claim():
    r = requests.get(SERVER + "/api/worker/claim", headers=HEAD, timeout=20)
    if r.status_code != 200:
        return None
    return (r.json() or {}).get("job")

def report_fail(jid, msg):
    try:
        requests.post("%s/api/worker/fail/%d" % (SERVER, jid), headers=HEAD,
                      json={"error": (msg or "")[-300:]}, timeout=20)
    except Exception:
        pass

def handle(job):
    jid = job["id"]; fmt = job.get("fmt", "mp3"); name = job.get("name") or ""
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, (name + ".%(ext)s") if name else "%(title).70s.%(ext)s")
        cmd = [YTDLP, "--no-playlist", "--restrict-filenames", "-x", "--audio-format", fmt]
        if os.path.isfile(DENO):                       # full YouTube extraction needs a JS runtime
            cmd += ["--js-runtimes", "deno:" + DENO]
        cmd += ["-o", out, job["url"]]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except Exception as e:
            report_fail(jid, "worker: %s" % e); return
        if r.returncode != 0:
            report_fail(jid, r.stderr); return
        files = [f for f in glob.glob(os.path.join(d, "*"))
                 if f.lower().endswith((".mp3", ".wav"))]
        if not files:
            report_fail(jid, "worker: no output file"); return
        path = files[0]
        try:
            with open(path, "rb") as fh:
                resp = requests.post("%s/api/worker/result/%d" % (SERVER, jid), headers=HEAD,
                                     files={"file": (os.path.basename(path), fh)}, timeout=300)
            print("job %d done → %s" % (jid, os.path.basename(path)), flush=True)
        except Exception as e:
            print("job %d upload failed: %s" % (jid, e), flush=True)
            report_fail(jid, "worker upload: %s" % e)

def main():
    if not TOKEN:
        raise SystemExit("SS_WORKER_TOKEN not set")
    print("download_worker → %s (yt-dlp: %s)" % (SERVER, YTDLP), flush=True)
    while True:
        try:
            job = claim()
        except Exception:
            time.sleep(5); continue
        if not job:
            time.sleep(POLL_IDLE); continue
        print("job %s: %s" % (job["id"], job["url"]), flush=True)
        handle(job)

if __name__ == "__main__":
    main()
