"""Real user accounts (email + username + password) with admin approval.
Gates the Add/Edit tab: only admins and APPROVED accounts can edit.
Accounts persist in accounts.json (gitignored); passwords hashed (werkzeug).
On registration the admin gets an approval email with one-click approve/deny links.

init(webapp, ctx) registers routes and returns check_login(login, pw)->account|None.
ctx: accounts_file, logger, base_url, admin_email, send_email(subject, html_body)->bool,
     is_admin()->bool
"""
import json
import os
import html
import time
import threading
import secrets as _secrets_mod
from werkzeug.security import generate_password_hash, check_password_hash
from flask import request, jsonify

_LOCK = threading.Lock()


def init(webapp, ctx):
    AF = ctx["accounts_file"]
    log = ctx["logger"]
    base_url = ctx.get("base_url", "")
    admin_email = ctx.get("admin_email", "")
    send_email = ctx["send_email"]
    is_admin = ctx["is_admin"]

    def _load():
        try:
            with open(AF) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(d):
        try:
            tmp = AF + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f, indent=1)
            os.replace(tmp, AF)
        except Exception as e:
            log.warning("[accounts] save: %r" % e)

    def _norm(s):
        return (s or "").strip().lower()

    def _page(msg):
        return ("<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
                "<body style='font-family:system-ui;background:#0f0f1e;color:#e0e0e0;text-align:center;padding:60px 20px'>"
                "<h2 style='color:#a78bfa'>🔊 Soundserver</h2><p style='font-size:1.1rem'>%s</p>"
                "<p><a href='/' style='color:#7dd3fc'>Go to soundboard</a></p></body></html>" % msg)

    # ---- registration ----
    @webapp.route("/api/register", methods=["POST"])
    def api_register():
        data = request.get_json(silent=True) or {}
        email = _norm(data.get("email"))
        username = (data.get("username") or "").strip()[:24]
        pw = data.get("password") or ""
        dom = email.split("@")[-1] if "@" in email else ""
        if not email or "@" not in email or "." not in dom:
            return jsonify({"error": "A valid email is required"}), 400
        if len(username) < 2:
            return jsonify({"error": "Pick a username (2+ chars)"}), 400
        if len(pw) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400
        with _LOCK:
            d = _load()
            existing = d.get(email)
            if existing and existing.get("status") == "approved":
                return jsonify({"error": "That email is already approved — just log in"}), 400
            tok = _secrets_mod.token_urlsafe(24)
            d[email] = {"email": email, "username": username,
                        "hash": generate_password_hash(pw), "status": "pending",
                        "token": tok, "created": time.time()}
            _save(d)
        try:
            approve = base_url + "/approve/" + tok + "?a=approve"
            deny = base_url + "/approve/" + tok + "?a=deny"
            body = ("<div style='font-family:system-ui'>"
                    "<p><b>%s</b> (%s) requested Add/Edit access to the soundboard.</p>"
                    "<p style='font-size:1.1rem'>"
                    "<a href='%s' style='background:#16a34a;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none'>✅ Approve</a>"
                    "&nbsp;&nbsp;"
                    "<a href='%s' style='background:#dc2626;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none'>❌ Deny</a>"
                    "</p></div>" % (html.escape(username), html.escape(email), approve, deny))
            send_email("Soundboard access request: %s" % username, body)
        except Exception as e:
            log.warning("[accounts] register email: %r" % e)
        return jsonify({"status": "pending"})

    # ---- one-click approve/deny from the email (token authorizes) ----
    @webapp.route("/approve/<token>")
    def approve(token):
        action = request.args.get("a", "approve")
        with _LOCK:
            d = _load()
            target = None
            for em, a in d.items():
                if a.get("token") == token:
                    target = em
                    break
            if not target:
                return _page("This link has expired or was already used.")
            uname = html.escape(d[target].get("username", ""))
            if action == "deny":
                d.pop(target, None)
                _save(d)
                return _page("Denied and removed: <b>%s</b>." % uname)
            d[target]["status"] = "approved"
            d[target]["token"] = ""
            _save(d)
        log.info("[accounts] approved %s" % target)
        return _page("✅ Approved! <b>%s</b> can now log in and use Add/Edit." % uname)

    # ---- admin fallback (approve from an in-app list, no email needed) ----
    @webapp.route("/api/accounts")
    def api_accounts():
        if not is_admin():
            return jsonify({"error": "Admin only"}), 403
        with _LOCK:
            d = _load()
        return jsonify({"accounts": [{"email": a["email"], "username": a.get("username", ""),
                                      "status": a.get("status", "")} for a in d.values()]})

    @webapp.route("/api/accounts/decide", methods=["POST"])
    def api_accounts_decide():
        if not is_admin():
            return jsonify({"error": "Admin only"}), 403
        data = request.get_json(silent=True) or {}
        email = _norm(data.get("email"))
        action = data.get("action", "approve")
        with _LOCK:
            d = _load()
            if email not in d:
                return jsonify({"error": "not found"}), 404
            if action == "deny":
                d.pop(email, None)
            else:
                d[email]["status"] = "approved"
                d[email]["token"] = ""
            _save(d)
        return jsonify({"status": "ok"})

    # ---- login check (used by the main login route) ----
    def check_login(login, pw):
        login = _norm(login)
        with _LOCK:
            d = _load()
            acct = d.get(login)
            if not acct:
                for a in d.values():
                    if a.get("username", "").lower() == login:
                        acct = a
                        break
        if acct and acct.get("status") == "approved" and check_password_hash(acct.get("hash", ""), pw):
            return acct
        return None

    log.info("\U0001F464 Accounts enabled (/api/register, /approve, /api/accounts)")
    return check_login
