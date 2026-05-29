/* Clip Studio — inline audio clip editor (the default Add-tab UI).
 * Source = link (YouTube/TikTok/etc.), upload, or an existing library sound.
 * Trim with a draggable waveform region, adjust volume, save as new song/sfx,
 * overwrite in place, or (admin) delete. Talks to /api/clip/*. */
(function () {
  "use strict";

  var SID = null, ORIGIN = null;
  var ORIG_NAME = "", ORIG_KIND = "", ORIG_CAT = "";
  var ws = null, regions = null, region = null;
  var loopOn = true, cropDepth = 0, playing = false, isAdmin = false, pollTimer = null;

  function el(tag, attrs, kids) {
    var e = document.createElement(tag); attrs = attrs || {};
    for (var k in attrs) { if (k === "style") e.style.cssText = attrs[k]; else if (k === "html") e.innerHTML = attrs[k]; else e.setAttribute(k, attrs[k]); }
    (kids || []).forEach(function (c) { e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function $(id) { return document.getElementById(id); }
  function show(e, on) { if (e) e.style.display = on ? "" : "none"; }
  function fmt(t) { t = Math.max(0, t || 0); var m = Math.floor(t / 60), s = (t % 60); return m + ":" + (s < 10 ? "0" : "") + s.toFixed(2); }
  function setStatus(msg, color) { var s = $("cc-status"); if (s) { s.textContent = msg || ""; s.style.color = color || "#9ca3af"; } }
  function jsonOrThrow(r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || "error"); return d; }); }

  function injectStyles() {
    if ($("cc-studio-style")) return;
    var css =
      "#cc-studio{color:#e0e0e0;font-family:system-ui,-apple-system,sans-serif;}" +
      "#cc-studio h2{margin:0 0 12px;font-size:1.1rem;color:#a78bfa;}" +
      "#cc-studio input,#cc-studio select{width:100%;padding:11px;border:1px solid #333;border-radius:8px;background:#16213e;color:#e0e0e0;font-size:.95rem;margin-bottom:8px;box-sizing:border-box;}" +
      "#cc-studio input[type=range]{padding:0;}" +
      ".cc-tabrow{display:flex;gap:6px;margin-bottom:10px;}" +
      ".cc-tab{flex:1;padding:9px;border:2px solid #333;border-radius:8px;background:#16213e;color:#aaa;font-weight:600;cursor:pointer;font-size:.85rem;text-align:center;}" +
      ".cc-tab.on{border-color:#7c3aed;background:#7c3aed;color:#fff;}" +
      ".cc-btn{padding:11px 16px;border:none;border-radius:8px;font-weight:700;cursor:pointer;font-size:.95rem;color:#fff;}" +
      ".cc-btn.primary{background:#7c3aed;}.cc-btn.go{background:#16a34a;}.cc-btn.warn{background:#d97706;}.cc-btn.danger{background:#dc2626;}.cc-btn.ghost{background:#374151;}" +
      ".cc-btn:disabled{opacity:.5;cursor:default;}" +
      ".cc-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}" +
      "#cc-wave{background:#0a0a14;border:1px solid #2a2a45;border-radius:8px;padding:6px;margin:10px 0;min-height:96px;}" +
      ".cc-time{font-size:.8rem;color:#9ca3af;}" +
      "#cc-status{min-height:20px;font-size:.85rem;margin-top:8px;}";
    document.head.appendChild(el("style", { id: "cc-studio-style", html: css }));
  }

  function studioHTML() {
    return '' +
      '<h2>✂️ Clip Studio</h2>' +
      '<div id="cc-step-src">' +
        '<div class="cc-tabrow">' +
          '<div id="cc-tab-youtube" class="cc-tab">Link</div>' +
          '<div id="cc-tab-upload" class="cc-tab">Upload</div>' +
          '<div id="cc-tab-existing" class="cc-tab">Edit existing</div></div>' +
        '<div id="cc-src-youtube"><input id="cc-url" type="text" placeholder="Paste a link — YouTube, TikTok, etc."></div>' +
        '<div id="cc-src-upload"><input id="cc-file" type="file" accept="audio/*,video/*"></div>' +
        '<div id="cc-src-existing">' +
          '<select id="cc-existing-kind"><option value="song">Songs</option><option value="sfx">SFX</option><option value="dcc">DCC</option></select>' +
          '<select id="cc-existing-file"></select></div>' +
        '<button id="cc-load-btn" class="cc-btn primary" style="width:100%;margin-top:6px;">Load for editing</button>' +
        '<div class="cc-time" style="text-align:center;margin-top:8px;">Tip: want the whole thing? Just don\'t move the selection — it defaults to the full clip.</div>' +
      '</div>' +
      '<div id="cc-step-edit" style="display:none;">' +
        '<div id="cc-wave"></div>' +
        '<div class="cc-row" style="justify-content:space-between;">' +
          '<div class="cc-row">' +
            '<button id="cc-play" class="cc-btn go">▶ Play selection</button>' +
            '<button id="cc-stop" class="cc-btn ghost">⏹</button>' +
            '<button id="cc-loop" class="cc-btn ghost">🔁 Loop: on</button></div>' +
          '<span id="cc-sel" class="cc-time"></span></div>' +
        '<div class="cc-row" style="margin-top:10px;">' +
          '<label class="cc-time" style="flex:1;">Start (s)<input id="cc-start" type="number" step="0.01" min="0"></label>' +
          '<label class="cc-time" style="flex:1;">End (s)<input id="cc-end" type="number" step="0.01" min="0"></label></div>' +
        '<div class="cc-row" style="margin-top:6px;">' +
          '<button id="cc-crop" class="cc-btn ghost">🔎 Crop to selection</button>' +
          '<button id="cc-uncrop" class="cc-btn ghost">↩ Uncrop</button>' +
          '<span class="cc-time" style="flex:1;">crop zooms in for finer control</span></div>' +
        '<div style="margin-top:10px;">' +
          '<label class="cc-time">🔊 Volume: <span id="cc-gainlbl">100%</span></label>' +
          '<input id="cc-gain" type="range" min="0" max="300" value="100" step="5">' +
          '<div class="cc-time" id="cc-gainnote"></div></div>' +
        '<hr style="border-color:#2a2a45;margin:14px 0;">' +
        '<input id="cc-name" type="text" placeholder="Clip name (e.g. light keikaku)">' +
        '<div class="cc-row"><label class="cc-time">Save as:</label>' +
          '<select id="cc-dest" style="flex:1;width:auto;"><option value="song">Song (group)</option><option value="sfx">SFX (category)</option></select></div>' +
        '<div id="cc-dest-song"><select id="cc-song-group"><option value="">-- pick group --</option></select>' +
          '<input id="cc-newgroup" type="text" placeholder="New group name" style="display:none;"></div>' +
        '<div id="cc-dest-sfx" style="display:none;"><select id="cc-sfx-cat"><option value="">-- pick category --</option></select>' +
          '<input id="cc-newcat" type="text" placeholder="New category name" style="display:none;"></div>' +
        '<div class="cc-row" style="margin-top:10px;">' +
          '<button id="cc-save" class="cc-btn primary">💾 Save clip</button>' +
          '<button id="cc-overwrite" class="cc-btn warn" style="display:none;">♻️ Overwrite</button>' +
          '<button id="cc-delete" class="cc-btn danger" style="display:none;">🗑 Delete</button>' +
          '<button id="cc-done" class="cc-btn ghost">Start over</button></div>' +
      '</div>' +
      '<div id="cc-status"></div>';
  }

  function mount() {
    var host = $("cc-studio-mount");
    if (!host || host._ccMounted) return;
    host._ccMounted = true;
    injectStyles();
    host.appendChild(el("div", { id: "cc-studio", html: studioHTML() }));

    ["youtube", "upload", "existing"].forEach(function (t) { $("cc-tab-" + t).addEventListener("click", function () { srcTab(t); }); });
    $("cc-load-btn").addEventListener("click", loadSource);
    $("cc-existing-kind").addEventListener("change", refreshExistingList);
    $("cc-play").addEventListener("click", playSel);
    $("cc-stop").addEventListener("click", stopPlay);
    $("cc-loop").addEventListener("click", function () { loopOn = !loopOn; $("cc-loop").textContent = loopOn ? "🔁 Loop: on" : "🔁 Loop: off"; });
    $("cc-start").addEventListener("change", inputsToRegion);
    $("cc-end").addEventListener("change", inputsToRegion);
    $("cc-crop").addEventListener("click", ccCrop);
    $("cc-uncrop").addEventListener("click", ccUncrop);
    $("cc-gain").addEventListener("input", gainChange);
    $("cc-dest").addEventListener("change", destChange);
    $("cc-song-group").addEventListener("change", function () { show($("cc-newgroup"), $("cc-song-group").value === "__new__"); });
    $("cc-sfx-cat").addEventListener("change", function () { show($("cc-newcat"), $("cc-sfx-cat").value === "__new__"); });
    $("cc-save").addEventListener("click", saveNew);
    $("cc-overwrite").addEventListener("click", overwrite);
    $("cc-delete").addEventListener("click", ccDelete);
    $("cc-done").addEventListener("click", resetToSource);

    fetch("/api/me").then(function (r) { return r.json(); }).then(function (d) { isAdmin = !!d.admin; }).catch(function () {});
    srcTab("youtube");
  }

  function srcTab(t) {
    ["youtube", "upload", "existing"].forEach(function (x) { $("cc-tab-" + x).classList.toggle("on", x === t); show($("cc-src-" + x), x === t); });
    if (t === "existing") refreshExistingList();
  }

  function refreshExistingList() {
    var kind = $("cc-existing-kind").value, sel = $("cc-existing-file");
    sel.innerHTML = "<option>Loading…</option>";
    fetch("/api/clip/list?kind=" + kind).then(function (r) { return r.json(); }).then(function (d) {
      sel.innerHTML = "";
      (d.items || []).forEach(function (it) {
        var o = el("option", { value: JSON.stringify({ filename: it.filename, category: it.category }) }); o.textContent = it.display; sel.appendChild(o);
      });
      if (!sel.options.length) sel.innerHTML = '<option value="">(none)</option>';
    }).catch(function () { sel.innerHTML = '<option value="">(error)</option>'; });
  }

  function loadSource() {
    var which = ["youtube", "upload", "existing"].filter(function (x) { return $("cc-tab-" + x).classList.contains("on"); })[0];
    setStatus("Loading…", "#4ecca3"); $("cc-load-btn").disabled = true;
    if (which === "existing") {
      var kind = $("cc-existing-kind").value, v = $("cc-existing-file").value;
      if (!v) { return failLoad(new Error("Pick a sound")); }
      var meta = JSON.parse(v);
      fetch("/api/clip/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: kind, filename: meta.filename, category: meta.category }) })
        .then(jsonOrThrow).then(function (d) { ORIGIN = "existing"; ORIG_KIND = kind; ORIG_CAT = meta.category; ORIG_NAME = d.name || meta.filename; SID = d.id; openEditor(d.duration); }).catch(failLoad);
    } else if (which === "upload") {
      var f = $("cc-file").files[0];
      if (!f) { return failLoad(new Error("Pick a file")); }
      var fd = new FormData(); fd.append("file", f);
      fetch("/api/clip/fetch", { method: "POST", body: fd }).then(jsonOrThrow).then(function (d) { ORIGIN = "upload"; SID = d.id; pollReady(); }).catch(failLoad);
    } else {
      var url = $("cc-url").value.trim();
      if (!url) { return failLoad(new Error("Need a link")); }
      fetch("/api/clip/fetch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: url }) })
        .then(jsonOrThrow).then(function (d) { ORIGIN = "link"; SID = d.id; setStatus("Downloading… (can take a bit)", "#4ecca3"); pollReady(); }).catch(failLoad);
    }
  }
  function failLoad(e) { setStatus(e.message || "Failed", "#e94560"); $("cc-load-btn").disabled = false; }

  function pollReady() {
    clearInterval(pollTimer);
    pollTimer = setInterval(function () {
      fetch("/api/clip/status/" + SID).then(function (r) { return r.json(); }).then(function (d) {
        if (d.status === "ready") { clearInterval(pollTimer); openEditor(d.duration); }
        else if (d.status === "error") { clearInterval(pollTimer); failLoad(new Error(d.error || "failed")); }
      }).catch(function () {});
    }, 1500);
  }

  function openEditor(duration) {
    setStatus("", ""); $("cc-load-btn").disabled = false;
    show($("cc-step-src"), false); show($("cc-step-edit"), true);
    show($("cc-overwrite"), ORIGIN === "existing");
    show($("cc-delete"), ORIGIN === "existing" && isAdmin);
    cropDepth = 0; updateUncropBtn();
    $("cc-gain").value = 100; gainChange();
    mountWave(duration, true);   // default selection = FULL clip
    $("cc-name").value = (ORIGIN === "existing") ? ORIG_NAME.replace(/\.wav$/, "") : "";
    populateGroups(); populateCats(); $("cc-dest").value = "song"; destChange();
  }

  function mountWave(duration, fullSelect) {
    var WS = window.WaveSurfer, RegionsPlugin = (WS && WS.Regions) || window.RegionsPlugin;
    if (ws) { try { ws.destroy(); } catch (e) {} ws = null; }
    $("cc-wave").innerHTML = ""; playing = false;
    ws = WS.create({ container: "#cc-wave", height: 90, waveColor: "#6d28d9", progressColor: "#a78bfa", cursorColor: "#f0f0f0", url: "/api/clip/audio/" + SID + "?v=" + Date.now() });
    regions = ws.registerPlugin(RegionsPlugin.create()); region = null;
    ws.on("decode", function () {
      var dur = ws.getDuration() || duration || 0;
      var end = fullSelect ? dur : Math.min(dur, dur > 8 ? 4 : dur);
      regions.clearRegions();
      region = regions.addRegion({ start: 0, end: end, color: "rgba(124,58,237,.25)", drag: true, resize: true });
      syncInputs(); applyGainPreview();
    });
    regions.on("region-updated", function (r) { region = r; syncInputs(); });
    ws.on("timeupdate", function (t) { if (region && playing && t >= region.end) { if (loopOn) ws.setTime(region.start); else stopPlay(); } });
  }

  function playSel() { if (region) { ws.setTime(region.start); ws.play(); playing = true; } }
  function stopPlay() { if (ws) ws.pause(); playing = false; }

  function syncInputs() {
    if (!region) return;
    $("cc-start").value = region.start.toFixed(2); $("cc-end").value = region.end.toFixed(2);
    $("cc-sel").textContent = "selection: " + fmt(region.start) + " → " + fmt(region.end) + "  (" + (region.end - region.start).toFixed(2) + "s)";
  }
  function inputsToRegion() {
    if (!region) return;
    var s = parseFloat($("cc-start").value) || 0, e = parseFloat($("cc-end").value) || 0;
    if (e <= s) e = s + 0.1; region.setOptions({ start: s, end: e }); syncInputs();
  }

  // ---- volume / gain ----
  function gainPct() { return parseInt($("cc-gain").value, 10) || 100; }
  function applyGainPreview() { if (ws) try { ws.setVolume(Math.min(1, gainPct() / 100)); } catch (e) {} }
  function gainChange() {
    var g = gainPct();
    $("cc-gainlbl").textContent = g + "%";
    $("cc-gainnote").textContent = g > 100 ? "(boost — preview is capped at 100%, full boost applies on save)" : "";
    applyGainPreview();
  }

  // ---- crop / uncrop ----
  function updateUncropBtn() { var b = $("cc-uncrop"); if (b) b.disabled = cropDepth <= 0; }
  function ccCrop() {
    var sel = curSel(); if (sel.end - sel.start < 0.1) return setStatus("Selection too short to crop", "#e94560");
    setStatus("Cropping…", "#4ecca3");
    fetch("/api/clip/crop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: SID, start: sel.start, end: sel.end }) })
      .then(jsonOrThrow).then(function (d) { cropDepth = d.depth; updateUncropBtn(); setStatus("🔎 Cropped (Uncrop to revert)", "#4ecca3"); mountWave(d.duration, true); }).catch(function (e) { setStatus(e.message || "Crop failed", "#e94560"); });
  }
  function ccUncrop() {
    setStatus("Reverting…", "#4ecca3");
    fetch("/api/clip/uncrop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: SID }) })
      .then(jsonOrThrow).then(function (d) { cropDepth = d.depth; updateUncropBtn(); setStatus(cropDepth ? "↩ Reverted one crop" : "↩ Back to full audio", "#9ca3af"); mountWave(d.duration, true); }).catch(function (e) { setStatus(e.message || "Uncrop failed", "#e94560"); });
  }

  function destChange() { var d = $("cc-dest").value; show($("cc-dest-song"), d === "song"); show($("cc-dest-sfx"), d === "sfx"); }
  function populateGroups() {
    var sel = $("cc-song-group");
    fetch("/api/songs").then(function (r) { return r.json(); }).then(function (d) {
      sel.innerHTML = '<option value="">-- pick group --</option>';
      (d.groups || []).forEach(function (g) { if (g.name !== "all") sel.appendChild(el("option", { value: g.name }, [g.name])); });
      sel.appendChild(el("option", { value: "__new__" }, ["➕ New group…"]));
    }).catch(function () {});
  }
  function populateCats() {
    var sel = $("cc-sfx-cat");
    fetch("/api/clip/list?kind=sfx").then(function (r) { return r.json(); }).then(function (d) {
      var cats = {}; (d.items || []).forEach(function (it) { if (it.category) cats[it.category] = 1; });
      ["react", "anime_quotes", "gaming", "meme_clips"].forEach(function (c) { cats[c] = 1; });
      sel.innerHTML = '<option value="">-- pick category --</option>';
      Object.keys(cats).sort().forEach(function (c) { sel.appendChild(el("option", { value: c }, [c])); });
      sel.appendChild(el("option", { value: "__new__" }, ["➕ New category…"]));
    }).catch(function () {});
  }

  function curSel() { return { start: parseFloat($("cc-start").value) || 0, end: parseFloat($("cc-end").value) || 0 }; }

  function saveNew() {
    var name = $("cc-name").value.trim(); if (!name) return setStatus("Need a name", "#e94560");
    var sel = curSel(); if (sel.end <= sel.start) return setStatus("End must be after start", "#e94560");
    var body = { id: SID, start: sel.start, end: sel.end, name: name, dest: $("cc-dest").value, gain: gainPct() / 100 };
    if (body.dest === "song") { var g = $("cc-song-group").value; if (g === "__new__") body.new_group = $("cc-newgroup").value.trim(); else if (g) body.group = g; }
    else { var c = $("cc-sfx-cat").value; body.category = (c === "__new__") ? $("cc-newcat").value.trim().toLowerCase().replace(/[^a-z0-9_]/g, "_") : c; if (!body.category) return setStatus("Need an SFX category", "#e94560"); }
    setStatus("Saving…", "#4ecca3"); $("cc-save").disabled = true;
    fetch("/api/clip/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(jsonOrThrow).then(function (d) {
        $("cc-save").disabled = false;
        setStatus("✅ Saved " + d.filename + (d.group ? " → " + d.group : "") + (d.category ? " → " + d.category : ""), "#4ecca3");
        if (window.loadSongs) try { window.loadSongs(); } catch (e) {}
        if (confirm("Saved! Grab another clip from this same source?")) { $("cc-name").value = ""; } else { resetToSource(); }
      }).catch(function (e) { $("cc-save").disabled = false; setStatus(e.message || "Save failed", "#e94560"); });
  }

  function overwrite() {
    var sel = curSel(); if (sel.end <= sel.start) return setStatus("End must be after start", "#e94560");
    if (!confirm('Overwrite "' + ORIG_NAME + '" with the selected segment?')) return;
    setStatus("Overwriting…", "#4ecca3"); $("cc-overwrite").disabled = true;
    fetch("/api/clip/overwrite", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: SID, start: sel.start, end: sel.end, gain: gainPct() / 100 }) })
      .then(jsonOrThrow).then(function (d) { $("cc-overwrite").disabled = false; setStatus("✅ Overwrote " + d.filename, "#4ecca3"); if (window.loadSongs) try { window.loadSongs(); } catch (e) {} }).catch(function (e) { $("cc-overwrite").disabled = false; setStatus(e.message || "Overwrite failed", "#e94560"); });
  }

  function ccDelete() {
    if (!confirm('Permanently delete "' + ORIG_NAME + '"? This cannot be undone.')) return;
    setStatus("Deleting…", "#4ecca3"); $("cc-delete").disabled = true;
    fetch("/api/clip/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: ORIG_KIND, category: ORIG_CAT, filename: ORIG_NAME }) })
      .then(jsonOrThrow).then(function (d) { $("cc-delete").disabled = false; setStatus("🗑 Deleted " + d.filename, "#9ca3af"); if (window.loadSongs) try { window.loadSongs(); } catch (e) {} resetToSource(); })
      .catch(function (e) { $("cc-delete").disabled = false; setStatus(e.message || "Delete failed", "#e94560"); });
  }

  function resetToSource() {
    stopPlay();
    if (ws) { try { ws.destroy(); } catch (e) {} ws = null; }
    if (SID) fetch("/api/clip/discard/" + SID, { method: "POST" }).catch(function () {});
    SID = null; ORIGIN = null; region = null; cropDepth = 0;
    show($("cc-step-edit"), false); show($("cc-step-src"), true); setStatus("", "");
    if ($("cc-existing-file")) refreshExistingList();
  }

  window.ccOpenStudio = function () { var a = document.querySelector('#mainTabs button'); if (a) try { a.click(); } catch (e) {} };

  if (document.readyState !== "loading") mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
