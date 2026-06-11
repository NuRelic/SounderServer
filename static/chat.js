/* Docked right-side Room panel — tabs: 💬 Chat · 📜 Log · 🎵 Top Songs · 💥 Top SFX.
 * Chat/Log poll /api/{chat,activity}?since (last 50 shown). Top tabs = /api/top
 * (vertical lists of clickable play buttons). Fresh from the Pi (no-store). */
(function () {
  "use strict";
  var CAP = 50;
  var chatSince = 0, logSince = 0, open = false, tab = "chat", unread = 0;

  var style = document.createElement("style");
  style.textContent =
    "#cc-room-tab{position:fixed;right:0;top:50%;transform:translateY(-50%);z-index:99998;writing-mode:vertical-rl;padding:14px 8px;border:none;border-radius:10px 0 0 10px;background:#2563eb;color:#fff;font-weight:700;font-size:.85rem;cursor:pointer;box-shadow:-3px 0 12px rgba(0,0,0,.35);font-family:system-ui,sans-serif;}" +
    "#cc-room-badge{background:#dc2626;border-radius:999px;padding:1px 6px;margin-top:6px;font-size:.7rem;display:none;writing-mode:horizontal-tb;}" +
    "#cc-room{position:fixed;top:0;right:0;height:100vh;width:320px;max-width:88vw;z-index:99999;display:flex;flex-direction:column;background:#0f0f1e;border-left:1px solid #2a2a45;box-shadow:-8px 0 40px rgba(0,0,0,.5);font-family:system-ui,sans-serif;transform:translateX(100%);transition:transform .2s ease;}" +
    "#cc-room.open{transform:translateX(0);}" +
    "#cc-room-head{display:flex;align-items:center;background:#16213e;}" +
    ".cc-tabbtn{flex:1;padding:12px 4px;background:none;border:none;color:#8b8ba0;font-weight:700;cursor:pointer;font-size:.95rem;}" +
    ".cc-tabbtn.on{color:#a78bfa;box-shadow:inset 0 -2px 0 #7c3aed;}" +
    "#cc-room-x{padding:0 12px;cursor:pointer;color:#888;font-size:1.05rem;}" +
    "#cc-chat-msgs,#cc-log-list{flex:1;overflow-y:auto;padding:10px 12px;display:flex;flex-direction:column;gap:7px;}" +
    "#cc-log-list{display:none;}" +
    ".cc-msg{font-size:.86rem;color:#e0e0e0;line-height:1.35;word-break:break-word;}" +
    ".cc-msg b{color:#7dd3fc;}" +
    ".cc-ev{font-size:.8rem;color:#9ca3af;line-height:1.3;word-break:break-word;}" +
    ".cc-ev b{color:#86efac;}" +
    "#cc-chat-input{display:flex;gap:6px;padding:9px;border-top:1px solid #2a2a45;}" +
    "#cc-chat-text{flex:1;padding:9px;border:1px solid #333;border-radius:8px;background:#16213e;color:#fff;font-size:.9rem;}" +
    "#cc-chat-send{padding:9px 14px;border:none;border-radius:8px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer;}" +
    ".cc-toplist{flex:1;overflow-y:auto;padding:10px;display:none;flex-direction:column;gap:5px;}" +
    ".cc-toph{color:#a78bfa;font-weight:700;font-size:.72rem;text-transform:uppercase;letter-spacing:.5px;margin:2px 2px 6px;}" +
    ".cc-topbtn{display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:#16213e;border:1px solid #2a2a45;color:#e0e0e0;border-radius:7px;padding:8px 10px;font-size:.82rem;cursor:pointer;line-height:1.3;}" +
    ".cc-topbtn:hover{background:#26345e;}" +
    ".cc-topbtn .nm{flex:1;white-space:normal;word-break:break-word;}" +
    ".cc-topbtn .n{color:#6b7280;margin-right:4px;}" +
    ".cc-topbtn b{color:#86efac;flex:0 0 auto;}" +
    "@media (min-width:820px){ body{margin-right:320px;} #cc-room{transform:translateX(0);} #cc-room-tab{display:none;} #cc-room-x{display:none;} }";
  document.head.appendChild(style);

  var tabBtn = document.createElement("button");
  tabBtn.id = "cc-room-tab";
  tabBtn.innerHTML = '💬 Room<span id="cc-room-badge">0</span>';
  document.body.appendChild(tabBtn);

  var panel = document.createElement("div");
  panel.id = "cc-room";
  panel.innerHTML =
    '<div id="cc-room-head">' +
      '<button class="cc-tabbtn on" id="cc-tab-chat" title="Chat">💬</button>' +
      '<button class="cc-tabbtn" id="cc-tab-log" title="Activity log">📜</button>' +
      '<button class="cc-tabbtn" id="cc-tab-songs" title="Top songs">🎵</button>' +
      '<button class="cc-tabbtn" id="cc-tab-sfx" title="Top sounds">💥</button>' +
      '<span id="cc-room-x">✕</span></div>' +
    '<div id="cc-chat-msgs"></div>' +
    '<div id="cc-log-list"></div>' +
    '<div id="cc-songs-list" class="cc-toplist"></div>' +
    '<div id="cc-sfx-list" class="cc-toplist"></div>' +
    '<div id="cc-chat-input"><input id="cc-chat-text" placeholder="Message…" maxlength="280" autocomplete="off"><button id="cc-chat-send">Send</button></div>';
  document.body.appendChild(panel);

  var badge = document.getElementById("cc-room-badge");
  var msgsEl = document.getElementById("cc-chat-msgs");
  var logEl = document.getElementById("cc-log-list");
  var songsEl = document.getElementById("cc-songs-list");
  var sfxEl = document.getElementById("cc-sfx-list");
  var textEl = document.getElementById("cc-chat-text");
  var inputBar = document.getElementById("cc-chat-input");

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function bump() { if (!open) { unread++; badge.textContent = unread; badge.style.display = "inline-block"; } }
  function trim(el) { while (el.children.length > CAP) el.removeChild(el.firstChild); }

  function addMsg(m) {
    if (m.id > chatSince) chatSince = m.id;
    var d = document.createElement("div"); d.className = "cc-msg";
    d.innerHTML = "<b>" + esc(m.name || "guest") + ":</b> " + esc(m.text);
    msgsEl.appendChild(d); trim(msgsEl); msgsEl.scrollTop = msgsEl.scrollHeight; bump();
  }
  function addEvent(e) {
    if (e.id > logSince) logSince = e.id;
    var d = document.createElement("div"); d.className = "cc-ev";
    d.innerHTML = "<b>" + esc(e.who) + "</b> " + esc(e.action) + (e.detail ? ": " + esc(e.detail) : "");
    logEl.appendChild(d); trim(logEl); logEl.scrollTop = logEl.scrollHeight;
    if (tab !== "log") bump();
  }

  function get(url) { return fetch(url, { cache: "no-store" }).then(function (r) { return r.json(); }); }
  function pollChat() { get("/api/chat?since=" + chatSince).then(function (d) { (d.messages || []).forEach(addMsg); }).catch(function () {}); }
  function pollLog() { get("/api/activity?since=" + logSince).then(function (d) { (d.events || []).forEach(addEvent); }).catch(function () {}); }

  function send() {
    var t = textEl.value.trim(); if (!t) return; textEl.value = "";
    fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: t }) }).then(pollChat).catch(function () {});
  }

  function renderList(el, heading, items, kind) {
    var h = '<div class="cc-toph">' + heading + '</div>';
    h += (items || []).map(function (it, i) {
      return '<button class="cc-topbtn" data-fn="' + esc(it.filename) + '" data-kind="' + kind +
             '" title="' + esc(it.display) + ' (' + it.count + ' plays)"><span class="nm"><span class="n">' + (i + 1) + '.</span> ' +
             esc(it.display) + '</span><b>' + it.count + '</b></button>';
    }).join("");
    el.innerHTML = h || '<div class="cc-toph">none yet</div>';
  }
  function fetchTop() {
    get("/api/top").then(function (d) {
      renderList(songsEl, "🎵 Most-played songs", d.songs, "song");
      renderList(sfxEl, "💥 Most-played sounds", d.sounds, "sound");
    }).catch(function () {});
  }
  function playTop(b) {
    fetch("/api/play/top", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: b.getAttribute("data-fn"), kind: b.getAttribute("data-kind") }) }).catch(function () {});
  }

  var TABS = { chat: msgsEl, log: logEl, songs: songsEl, sfx: sfxEl };
  function setTab(t) {
    tab = t;
    ["chat", "log", "songs", "sfx"].forEach(function (k) {
      document.getElementById("cc-tab-" + k).classList.toggle("on", k === t);
      TABS[k].style.display = (k === t) ? "flex" : "none";
    });
    inputBar.style.display = t === "chat" ? "flex" : "none";
    if (t === "songs" || t === "sfx") fetchTop();
    else { TABS[t].scrollTop = TABS[t].scrollHeight; }
  }
  function setOpen(o) {
    open = o;
    panel.classList.toggle("open", o);
    tabBtn.style.display = o ? "none" : "block";
    if (o) { unread = 0; badge.style.display = "none"; }
  }

  tabBtn.addEventListener("click", function () { setOpen(true); });
  document.getElementById("cc-room-x").addEventListener("click", function () { setOpen(false); });
  ["chat", "log", "songs", "sfx"].forEach(function (k) {
    document.getElementById("cc-tab-" + k).addEventListener("click", function () { setTab(k); });
  });
  function delegateTop(el) {
    el.addEventListener("click", function (e) { var b = e.target.closest && e.target.closest(".cc-topbtn"); if (b) playTop(b); });
  }
  delegateTop(songsEl); delegateTop(sfxEl);
  document.getElementById("cc-chat-send").addEventListener("click", send);
  textEl.addEventListener("keydown", function (e) { if (e.key === "Enter") send(); });

  // Desktop: panel always present (CSS pushes content left so nothing is covered).
  var _deskMq = window.matchMedia("(min-width: 820px)");
  function _applyMode() { if (_deskMq.matches) setOpen(true); }
  _applyMode();
  if (_deskMq.addEventListener) _deskMq.addEventListener("change", _applyMode);

  pollChat(); pollLog();
  setInterval(pollChat, 2000);
  setInterval(pollLog, 2000);
})();
