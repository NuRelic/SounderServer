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
  var on = false, unlocked = false, timer = null, isAdmin = false, browserVol = 1;
  try { var _bv = localStorage.getItem("cc_bvol"); if (_bv !== null) browserVol = Math.max(0, Math.min(1, parseFloat(_bv))); } catch (e) {}
  var pool = {};   // token -> Audio element
  var SILENT = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";

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
          a.volume = browserVol;
          a.play().catch(function () {});
          pool[s.token] = a;
        }
        if (s.paused) { if (!a.paused) a.pause(); }
        else { if (a.paused) a.play().catch(function () {}); }
      });
      Object.keys(pool).forEach(function (tok) {
        if (!seen[tok]) { try { pool[tok].pause(); pool[tok].src = ""; } catch (e) {} delete pool[tok]; }
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
    if (!on) return;
    [120, 300, 550].forEach(function (ms) { setTimeout(pollActive, ms); });
  }, true);

  start();
  pollPresence(); setInterval(pollPresence, 8000);
  document.addEventListener("pointerdown", unlock, { once: true, capture: true });
  document.addEventListener("touchstart", unlock, { once: true, capture: true });
})();
