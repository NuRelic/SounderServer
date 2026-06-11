/* Layered event-driven browser audio + presence.
 * Polls /api/active and plays EACH active sound (song or sfx) in its own <audio>
 * (one per token), so overlapping kitchen audio is mirrored in the browser.
 * Also renders who's online and who's playing what. Defaults to listening;
 * unlocks audio on the first user gesture (browser autoplay policy). */
(function () {
  "use strict";
  var btn = document.getElementById("cc-stream-btn");
  var prim = document.getElementById("cc-stream-audio");   // used only for the gesture unlock
  if (!btn || !prim) return;
  var ico = document.getElementById("cc-stream-ico");
  var lbl = document.getElementById("cc-stream-label");
  var on = false, unlocked = false, timer = null, isAdmin = false, browserVol = 1, lastClick = 0;
  try { var _bv = localStorage.getItem("cc_bvol"); if (_bv !== null) browserVol = Math.max(0, Math.min(1, parseFloat(_bv))); } catch (e) {}
  var pool = {};   // token -> Audio element
  var SILENT = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";

  var SYNC_BUFFER = 1.0;   // predictable lag (s) in sync mode
  var syncOn = true; try { var _sv = localStorage.getItem("cc_sync"); if (_sv !== null) syncOn = (_sv === "1"); } catch (e) {}   // default ON
  var clockOffset = 0, _bestRtt = Infinity;
  function syncedNow() { return (Date.now() + clockOffset) / 1000; }   // server epoch seconds
  function clockSample() {
    var t0 = Date.now();
    fetch("/api/time", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (d) {
      var t1 = Date.now(), rtt = t1 - t0;
      if (rtt < _bestRtt) { _bestRtt = rtt; clockOffset = d.t * 1000 - (t0 + rtt / 2); }
    }).catch(function () {});
  }
  for (var _i = 0; _i < 5; _i++) setTimeout(clockSample, _i * 250);
  setInterval(clockSample, 20000);
  function schedulePlay(a, tok) {
    var head = (syncedNow() - a._start) - SYNC_BUFFER;   // position on the shared timeline
    if (head < -0.03) {                                  // not started yet -> wait, then play from 0
      a._waiting = true; try { a.currentTime = 0; } catch (e) {}
      setTimeout(function () { if (pool[tok] === a) { a._waiting = false; a.play().catch(function () {}); } }, (-head) * 1000);
    } else if (!a._dur || head < a._dur) {               // late -> seek in to catch up
      a._waiting = false; try { a.currentTime = Math.max(0, head); } catch (e) {}
      a.play().catch(function () {});
    } else { a._waiting = false; }                        // already over -> skip
  }

  function setOff() { on = false; ico.innerHTML = "&#128266;"; lbl.textContent = "Listen in this browser"; btn.style.background = "#6d28d9"; btn.style.animation = "ccPulse 2.2s infinite"; }
  function setOn() { on = true; ico.innerHTML = "&#128261;"; lbl.textContent = "● Listening — tap to stop"; btn.style.background = "#dc2626"; btn.style.animation = "none"; }

  function unlock() {
    if (unlocked) return; unlocked = true;
    try { prim.src = SILENT; prim.play().catch(function () {}); } catch (e) {}
    if (on) pollActive();
  }

  function pollActive() {
    if (!on) return;
    fetch("/api/active").then(function (r) { return r.json(); }).then(function (d) {
      if (!on) return;   // stopped while this poll was in flight
      var act = d.active || [], seen = {};
      act.forEach(function (s) {
        seen[s.token] = 1;
        var a = pool[s.token];
        if (!a) {
          a = new Audio("/api/active/" + s.token + ".mp3");
          a.volume = browserVol; a._start = s.start || 0; a._dur = s.duration || 0;
          pool[s.token] = a;
          a.addEventListener("playing", function () {
            if (lastClick && (Date.now() - lastClick) <= 4000) {
              var ms = Date.now() - lastClick; lastClick = 0;
              var el = document.getElementById("cc-latency"); if (el) el.textContent = "⏱ " + ms + "ms";
              fetch("/api/metric", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ms: ms, sync: syncOn }) }).catch(function () {});
            }
          }, { once: true });
          if (syncOn && a._start) schedulePlay(a, s.token);
          else a.play().catch(function () {});
        }
        if (s.paused) { if (!a.paused) a.pause(); }
        else if (!a._waiting) { if (a.paused) a.play().catch(function () {}); }
      });
      Object.keys(pool).forEach(function (tok) {
        if (seen[tok]) return;
        // Sound left the server's active list. Because the browser runs ~SYNC_BUFFER
        // behind the server (which prunes at the sound's true end), hard-pausing here
        // clips the tail — fatal for short SFX. So if we're within the buffer of the
        // natural end, let it play out; only stop now if lots of time is left (a real
        // admin kill mid-playback).
        var a = pool[tok];
        var mediaDur = isFinite(a.duration) ? a.duration : (a._dur || 0);
        var rem = mediaDur ? (mediaDur - a.currentTime) : 0;
        if (!a.paused && rem > SYNC_BUFFER + 0.6) {
          try { a.pause(); a.src = ""; } catch (e) {}
          delete pool[tok];
        } else if (a.paused) {
          try { a.src = ""; } catch (e) {}
          delete pool[tok];
        } else if (!a._finishing) {
          a._finishing = true;
          var done = function () { try { a.pause(); a.src = ""; } catch (e) {} delete pool[tok]; };
          a.addEventListener("ended", done, { once: true });
          setTimeout(done, Math.max(250, (rem + 0.5) * 1000));   // safety if 'ended' never fires
        }
      });
      renderActive(act);
    }).catch(function () {});
  }

  function stopAll() {
    Object.keys(pool).forEach(function (tok) { try { pool[tok].pause(); pool[tok].src = ""; } catch (e) {} delete pool[tok]; });
    renderActive([]);
  }

  function renderActive(act) {
    var el = document.getElementById("cc-active"); if (!el) return;
    if (!act.length) { el.textContent = ""; return; }
    el.innerHTML = act.map(function (s) {
      var label = (s.kind === "song" ? "🎵 " : "🔊 ") + escapeHtml(s.name || "sound") + (s.by ? ' <span style="opacity:.7">· ' + escapeHtml(s.by) + "</span>" : "");
      var kill = isAdmin ? ' <span class="cc-kill" data-token="' + s.token + '" title="kill this" style="cursor:pointer;color:#f87171;font-weight:700;">\u2715</span>' : "";
      return label + kill;
    }).join("  &nbsp; ");
  }
  function renderPresence(users) {
    var el = document.getElementById("cc-online"); if (!el) return;
    el.textContent = "👥 " + users.length + (users.length === 1 ? " here" : " here") + (users.length ? ": " + users.join(", ") : "");
  }
  function escapeHtml(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function pollPresence() { fetch("/api/presence").then(function (r) { return r.json(); }).then(function (d) { renderPresence(d.users || []); }).catch(function () {}); }

  function start() { setOn(); pollActive(); if (!timer) timer = setInterval(pollActive, 600); }
  function stop() { if (timer) { clearInterval(timer); timer = null; } stopAll(); setOff(); }

  btn.addEventListener("click", function () { unlock(); if (!on) start(); else stop(); });

  // default to listening; presence updates regardless; unlock on first gesture anywhere
  // admin: enable per-sound kill buttons
  fetch("/api/me").then(function (r) { return r.json(); }).then(function (d) { isAdmin = !!d.admin; }).catch(function () {});
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (t && t.classList && t.classList.contains("cc-kill")) {
      fetch("/api/active/" + t.getAttribute("data-token") + "/stop", { method: "POST" }).then(pollActive).catch(function () {});
    }
  });

  // per-person play limits: show to everyone, let admins change them
  function fetchLimits() {
    fetch("/api/limits", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (d) {
      var lim = document.getElementById("cc-limits");
      if (lim) lim.textContent = "🎚️ Max per person: " + d.songs_per_person + " song" + (d.songs_per_person > 1 ? "s" : "") + " · " + d.sfx_per_person + " sound" + (d.sfx_per_person > 1 ? "s" : "") + " at once";
      var si = document.getElementById("cc-lim-songs"), xi = document.getElementById("cc-lim-sfx");
      if (si && document.activeElement !== si) si.value = d.songs_per_person;
      if (xi && document.activeElement !== xi) xi.value = d.sfx_per_person;
    }).catch(function () {});
  }
  fetchLimits();
  setInterval(fetchLimits, 10000);
  var _applyBtn = document.getElementById("cc-lim-apply");
  if (_applyBtn) _applyBtn.addEventListener("click", function () {
    fetch("/api/limits", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ songs_per_person: parseInt(document.getElementById("cc-lim-songs").value, 10),
                             sfx_per_person: parseInt(document.getElementById("cc-lim-sfx").value, 10) }) }).then(fetchLimits).catch(function () {});
  });

  var _bvSlider = document.getElementById("cc-browser-vol");
  if (_bvSlider) {
    _bvSlider.value = Math.round(browserVol * 100);
    _bvSlider.addEventListener("input", function () {
      browserVol = (parseInt(_bvSlider.value, 10) || 0) / 100;
      Object.keys(pool).forEach(function (t) { try { pool[t].volume = browserVol; } catch (e) {} });
      try { localStorage.setItem("cc_bvol", browserVol); } catch (e) {}
    });
  }

  // responsiveness: a click is probably a play — poll right away a few times
  document.addEventListener("click", function () {
    lastClick = Date.now();
    if (!on) return;
    [120, 300, 550].forEach(function (ms) { setTimeout(pollActive, ms); });
  }, true);

  // sync mode: keep each sound locked to the shared timeline (catch up if behind)
  setInterval(function () {
    if (!syncOn) return;
    Object.keys(pool).forEach(function (tok) {
      var a = pool[tok];
      if (!a || a._waiting || a.paused || !a._start) return;
      var desired = (syncedNow() - a._start) - SYNC_BUFFER;
      if (desired < 0) return;
      var drift = a.currentTime - desired;
      if (Math.abs(drift) > 0.35) { try { a.currentTime = desired; } catch (e) {} a.playbackRate = 1; }
      else if (Math.abs(drift) > 0.08) { a.playbackRate = drift > 0 ? 0.96 : 1.04; }
      else { a.playbackRate = 1; }
    });
  }, 1200);

  var _syncBtn = document.getElementById("cc-sync-btn");
  function renderSyncBtn() { if (_syncBtn) { _syncBtn.textContent = syncOn ? "🔁 Sync: ON (~1s)" : "🔁 Sync: off"; _syncBtn.style.background = syncOn ? "#16a34a" : "#374151"; } }
  if (_syncBtn) {
    renderSyncBtn();
    _syncBtn.addEventListener("click", function () {
      syncOn = !syncOn; try { localStorage.setItem("cc_sync", syncOn ? "1" : "0"); } catch (e) {}
      renderSyncBtn();
      Object.keys(pool).forEach(function (tok) { try { pool[tok].pause(); pool[tok].src = ""; } catch (e) {} delete pool[tok]; });
      if (on) pollActive();
    });
  }

  start();
  pollPresence(); setInterval(pollPresence, 8000);
  document.addEventListener("pointerdown", unlock, { once: true, capture: true });
  document.addEventListener("touchstart", unlock, { once: true, capture: true });
})();
