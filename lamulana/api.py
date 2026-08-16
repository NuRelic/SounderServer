"""HTTP surface for the La-Mulana 2 tracker.

Read it in order: helpers, then clues, then threads, then the link between them,
then search and the checklist. The thread routes are the ones with real behavior
-- solving a thread reaches back into the clues that fed it.
"""

import os
import time

from flask import Blueprint, jsonify, render_template, request, session

from . import db as _db

bp = Blueprint("lamulana", __name__, url_prefix="/lamulana")

DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "lamulana.db")

CLUE_STATES = ("raw", "understood", "used")
CLUE_SOURCES = ("tablet", "npc", "mail", "other")
THREAD_STATES = ("open", "solved")


def _conn():
    """This request thread's connection. A dict lookup on a threading.local."""
    return _db.get_conn(DB_PATH)


# Schema and seed run once here at import, on the importing thread, not
# per-thread inside _conn(). Both are idempotent, but running them on every
# thread's first request would mean a burst of redundant writes contending for
# the write lock during the first seconds after a deploy.
_db.init_schema(_conn())
_db.seed_all(_conn())


def can_edit():
    return bool(session.get("admin") or session.get("can_edit"))


def need_edit():
    """An error response if this session may not write, else None."""
    if not can_edit():
        return jsonify({"error": "login required"}), 403
    return None


def _body():
    return request.get_json(silent=True) or {}


def _now():
    return int(time.time())


@bp.route("/")
def page():
    return render_template("lamulana.html")


@bp.route("/api/bootstrap")
def api_bootstrap():
    """Everything the page needs on load, in one request."""
    areas = [dict(r) for r in _conn().execute(
        "SELECT id, name, position FROM area ORDER BY position"
    )]
    counts = {
        "clues": _conn().execute("SELECT COUNT(*) FROM clue").fetchone()[0],
        "clues_understood": _conn().execute(
            "SELECT COUNT(*) FROM clue WHERE state = 'understood'").fetchone()[0],
        "threads_open": _conn().execute(
            "SELECT COUNT(*) FROM thread WHERE state = 'open'").fetchone()[0],
    }
    return jsonify({"areas": areas, "checklist": _checklist_groups(), "counts": counts})


def _checklist_groups():
    rows = _conn().execute(
        "SELECT id, group_name, name, position, done, done_at, note"
        " FROM checklist_item ORDER BY group_name, position, id"
    ).fetchall()
    order = []
    by_group = {}
    for r in rows:
        if r["group_name"] not in by_group:
            by_group[r["group_name"]] = []
            order.append(r["group_name"])
        item = dict(r)
        item.pop("group_name")
        item["done"] = bool(item["done"])
        by_group[r["group_name"]].append(item)
    return [{"group": g, "items": by_group[g]} for g in order]
