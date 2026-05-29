/* Docked right-side panel with two tabs: 💬 Chat and 📜 Log (who did what).
 * Chat: /api/chat (POST send, GET ?since). Log: /api/activity (GET ?since).
 * Always polls fresh from the Pi every 2s (server also sends Cache-Control: no-store). */
(function () {
  "use strict";
  var chatSince = 0, logSince = 0, open = false, tab = "chat", unread = 0;

  var style = document.createElement("style");
  style.textContent =
    "#cc-room-tab{position:fixed;right:0;top:50%;transform:translateY(-50%);z-index:99998;writing-mode:vertical-rl;padding:14px 8px;border:none;border-radius:10px 0 0 10px;background:#2563eb;color:#fff;font-weight:700;font-size:.85rem;cursor:pointer;box-shadow:-3px 0 12px rgba(0,0,0,.35);font-family:system-ui,sans-serif;}" +
    "#cc-room-badge{background:#dc2626;border-radius:999px;padding:1px 6px;margin-top:6px;font-size:.7rem;display:none;writing-mode:horizontal-tb;}" +
    "#cc-room{position:fixed;top:0;right:0;height:100vh;width:320px;max-width:88vw;z-index:99999;display:flex;flex-direction:column;background:#0f0f1e;border-left:1px solid #2a2a45;box-shadow:-8px 0 40px rgba(0,0,0,.5);font-family:system-ui,sans-serif;transform:translateX(100%);transition:transform .2s ease;}" +
    "#cc-room.open{transform:translateX(0);}" +
    "#cc-room-head{display:flex;align-items:center;background:#16213e;}" +
    ".cc-tabbtn{flex:1;padding:13px 8px;background:none;border:none;color:#8b8ba0;font-weight:700;cursor:pointer;font-size:.9rem;}" +
    ".cc-tabbtn.on{color:#a78bfa;box-shadow:inset 0 -2px 0 #7c3aed;}" +
    "#cc-room-x{padding:0 14px;cursor:pointer;color:#888;font-size:1.1rem;}" +
    "#cc-chat-msgs,#cc-log-list{flex:1;overflow-y:auto;padding:10px 12px;display:flex;flex-direction:column;gap:7px;}" +
    "#cc-log-list{display:none;}" +
    ".cc-msg{font-size:.86rem;color:#e0e0e0;line-height:1.35;word-break:break-word;}" +
    ".cc-msg b{color:#7dd3fc;}" +
    ".cc-ev{font-size:.8rem;color:#9ca3af;line-height:1.3;word-break:break-word;}" +
    ".cc-ev b{color:#86efac;}" +
    "#cc-chat-input{display:flex;gap:6px;padding:9px;border-top:1px solid #2a2a45;}" +
    "#cc-chat-text{flex:1;padding:9px;border:1px solid #333;border-radius:8px;background:#16213e;color:#fff;font-size:.9rem;}" +
    "#cc-chat-send{padding:9px 14px;border:none;border-radius:8px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer;}" +
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
      '<button class="cc-tabbtn on" id="cc-tab-chat">💬 Chat</button>' +
      '<button class="cc-tabbtn" id="cc-tab-log">📜 Log</button>' +
      '<span id="cc-room-x">✕</span></div>' +
    '<div id="cc-chat-msgs"></div>' +
    '<div id="cc-log-list"></div>' +
    '<div id="cc-chat-input"><input id="cc-chat-text" placeholder="Message…" maxlength="280" autocomplete="off"><button id="cc-chat-send">Send</button></div>';
  document.body.appendChild(panel);

  var badge = document.getElementById("cc-room-badge");
  var msgsEl = document.getElementById("cc-chat-msgs");
  var logEl = document.getElementById("cc-log-list");
  var textEl = document.getElementById("cc-chat-text");
  var inputBar = document.getElementById("cc-chat-input");

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function bump() { if (!open) { unread++; badge.textContent = unread; badge.style.display = "inline-block"; } }

  function addMsg(m) {
    if (m.id > chatSince) chatSince = m.id;
    var d = document.createElement("div"); d.className = "cc-msg";
    d.innerHTML = "<b>" + esc(m.name || "guest") + ":</b> " + esc(m.text);
    msgsEl.appendChild(d); msgsEl.scrollTop = msgsEl.scrollHeight; bump();
  }
  function addEvent(e) {
    if (e.id > logSince) logSince = e.id;
    var d = document.createElement("div"); d.className = "cc-ev";
    d.innerHTML = "<b>" + esc(e.who) + "</b> " + esc(e.action) + (e.detail ? ": " + esc(e.detail) : "");
    logEl.appendChild(d); logEl.scrollTop = logEl.scrollHeight;
    if (tab !== "log") bump();
  }

  // cache:'no-store' belt-and-suspenders on top of the server's no-store header
  function get(url) { return fetch(url, { cache: "no-store" }).then(function (r) { return r.json(); }); }
  function pollChat() { get("/api/chat?since=" + chatSince).then(function (d) { (d.messages || []).forEach(addMsg); }).catch(function () {}); }
  function pollLog() { get("/api/activity?since=" + logSince).then(function (d) { (d.events || []).forEach(addEvent); }).catch(function () {}); }

  function send() {
    var t = textEl.value.trim(); if (!t) return; textEl.value = "";
    fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: t }) }).then(pollChat).catch(function () {});
  }

  function setTab(t) {
    tab = t;
    document.getElementById("cc-tab-chat").classList.toggle("on", t === "chat");
    document.getElementById("cc-tab-log").classList.toggle("on", t === "log");
    msgsEl.style.display = t === "chat" ? "flex" : "none";
    logEl.style.display = t === "log" ? "flex" : "none";
    inputBar.style.display = t === "chat" ? "flex" : "none";
    var box = t === "chat" ? msgsEl : logEl; box.scrollTop = box.scrollHeight;
  }
  function setOpen(o) {
    open = o;
    panel.classList.toggle("open", o);
    tabBtn.style.display = o ? "none" : "block";
    if (o) { unread = 0; badge.style.display = "none"; var box = tab === "chat" ? msgsEl : logEl; box.scrollTop = box.scrollHeight; }
  }

  tabBtn.addEventListener("click", function () { setOpen(true); });
  document.getElementById("cc-room-x").addEventListener("click", function () { setOpen(false); });
  document.getElementById("cc-tab-chat").addEventListener("click", function () { setTab("chat"); });
  document.getElementById("cc-tab-log").addEventListener("click", function () { setTab("log"); });
  document.getElementById("cc-chat-send").addEventListener("click", send);
  textEl.addEventListener("keydown", function (e) { if (e.key === "Enter") send(); });

  // Desktop: panel is always present (CSS pushes content left so nothing is covered).
  var _deskMq = window.matchMedia("(min-width: 820px)");
  function _applyMode() { if (_deskMq.matches) setOpen(true); }
  _applyMode();
  if (_deskMq.addEventListener) _deskMq.addEventListener("change", _applyMode);

  pollChat(); pollLog();
  setInterval(pollChat, 2000);
  setInterval(pollLog, 2000);
})();
