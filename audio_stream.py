"""
Browser audio streaming for the soundboard (server-side "file mirror").

Design goal: be COMPLETELY DECOUPLED from the pygame/ALSA room-audio path.
Nothing here opens the audio device. If anything in this module fails, the
soundboard's kitchen-speaker playback is physically unaffected.

How it works:
  * A single shared ffmpeg process encodes the *currently playing web track*
    (the same file pygame is playing on web_channel) to a live MP3 stream.
  * That MP3 is fanned out to every connected browser via per-client queues.
  * ffmpeg only runs while at least one browser is listening (no idle CPU).
  * New listeners join the live position; ffmpeg seeks to the room's current
    offset so the browser stays roughly in sync with the kitchen.

Public API:
  broadcaster = Broadcaster(state_fn)   # state_fn() -> dict(path, playing, paused, start)
  broadcaster.start()
  Response(broadcaster.stream(), mimetype="audio/mpeg")
"""

import queue
import subprocess
import threading
import time

FFMPEG = "ffmpeg"
CHUNK = 1024                    # small reads -> bytes reach clients promptly
CLIENT_QUEUE_MAX = 48           # ~3s cap at 128kbps; drops oldest to stay near-live
MANAGER_TICK = 0.2             # quicker song pickup
MP3_BITRATE = "128k"
# low-latency ffmpeg flags
_LL_IN = ["-fflags", "+nobuffer"]
_LL_OUT = ["-write_xing", "0", "-flush_packets", "1"]


class Broadcaster:
    def __init__(self, state_fn, logger=None):
        # state_fn returns: {"path": str|None, "playing": bool, "paused": bool, "start": float}
        self._state_fn = state_fn
        self._log = logger
        self._subs = set()
        self._subs_lock = threading.Lock()
        self._stop = False
        self._proc = None
        self._reader = None
        self._cur_id = None       # ("silence",) or ("file", path) currently encoding
        self._mgr = None

    # ---- logging helper (never raises) -------------------------------------
    def _logmsg(self, msg):
        try:
            if self._log:
                self._log.info("[stream] " + msg)
        except Exception:
            pass

    # ---- subscriber management ---------------------------------------------
    def subscribe(self):
        q = queue.Queue(maxsize=CLIENT_QUEUE_MAX)
        with self._subs_lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._subs_lock:
            self._subs.discard(q)

    def _sub_count(self):
        with self._subs_lock:
            return len(self._subs)

    def _broadcast(self, data):
        with self._subs_lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(data)
            except queue.Full:
                # Slow client: drop its oldest chunk and push the newest so the
                # stream stays live rather than stalling.
                try:
                    q.get_nowait()
                    q.put_nowait(data)
                except Exception:
                    pass

    # ---- ffmpeg source management ------------------------------------------
    def _silence_cmd(self):
        return [FFMPEG, "-nostdin", "-loglevel", "quiet"] + _LL_IN + [
            "-re", "-f", "lavfi", "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:a", "libmp3lame", "-b:a", "64k"] + _LL_OUT + ["-f", "mp3", "pipe:1"]

    def _file_cmd(self, path, offset):
        cmd = [FFMPEG, "-nostdin", "-loglevel", "quiet"] + _LL_IN
        if offset and offset > 0.5:
            cmd += ["-ss", "%.2f" % offset]
        cmd += [
            "-re", "-i", path,
            "-ac", "2", "-ar", "44100",
            "-c:a", "libmp3lame", "-b:a", MP3_BITRATE] + _LL_OUT + ["-f", "mp3", "pipe:1"]
        return cmd

    def _kill_proc(self):
        p = self._proc
        self._proc = None
        self._cur_id = None
        if p:
            try:
                p.kill()
            except Exception:
                pass

    def _start_source(self, cmd, ident):
        self._kill_proc()
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
            )
        except Exception as e:
            self._proc = None
            self._cur_id = None
            self._logmsg("ffmpeg spawn failed: %r" % e)
            return
        self._cur_id = ident
        proc = self._proc

        def _read(p):
            try:
                while True:
                    chunk = p.stdout.read(CHUNK)
                    if not chunk:
                        break
                    self._broadcast(chunk)
            except Exception:
                pass

        self._reader = threading.Thread(target=_read, args=(proc,), daemon=True)
        self._reader.start()
        self._logmsg("source -> %s" % (ident,))

    # ---- manager loop -------------------------------------------------------
    def _manage(self):
        while not self._stop:
            try:
                # No listeners: tear down ffmpeg, stay idle (zero CPU).
                if self._sub_count() == 0:
                    if self._proc is not None:
                        self._kill_proc()
                        self._logmsg("no listeners; source stopped")
                    time.sleep(MANAGER_TICK)
                    continue

                try:
                    st = self._state_fn() or {}
                except Exception:
                    st = {}

                path = st.get("path")
                playing = bool(st.get("playing"))
                paused = bool(st.get("paused"))
                want_silence = (not path) or (not playing) or paused

                proc_dead = (self._proc is not None and self._proc.poll() is not None)

                if want_silence:
                    if self._cur_id != ("silence",) or self._proc is None or proc_dead:
                        self._start_source(self._silence_cmd(), ("silence",))
                else:
                    ident = ("file", path)
                    if self._cur_id != ident or self._proc is None or proc_dead:
                        offset = 0.0
                        start = st.get("start")
                        if start:
                            offset = max(0.0, time.time() - float(start))
                        self._start_source(self._file_cmd(path, offset), ident)
            except Exception as e:
                self._logmsg("manager error: %r" % e)
            time.sleep(MANAGER_TICK)

    def start(self):
        if self._mgr is None:
            self._mgr = threading.Thread(target=self._manage, daemon=True)
            self._mgr.start()
            self._logmsg("broadcaster started")

    # ---- Flask response generator ------------------------------------------
    def stream(self):
        q = self.subscribe()
        try:
            while True:
                try:
                    chunk = q.get(timeout=15)
                except queue.Empty:
                    # keep the generator alive even if the source is momentarily
                    # absent (e.g. just-started, between sources)
                    continue
                if chunk is None:
                    break
                yield chunk
        finally:
            self.unsubscribe(q)
