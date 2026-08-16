"""HTTP surface for the La-Mulana 2 tracker.

Read it in order: helpers, then clues, then threads, then the link between them,
then search and the checklist. The thread routes are the ones with real behavior
-- solving a thread reaches back into the clues that fed it.

Only the page route and bootstrap exist so far. Clues, threads, the link
between them, and search land in Tasks 4-8 -- if you came here looking for
them, they are not written yet.
"""

import os
import time

from flask import Blueprint, jsonify, render_template, request

from auth import can_edit, need_edit

from . import db as _db
from .seed import CHECKLIST as _SEED_CHECKLIST

bp = Blueprint("lamulana", __name__, url_prefix="/lamulana")

DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "lamulana.db")

# Used by Task 4 to validate request bodies for the clue/thread routes it adds.
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
    ).fetchall()]
    counts = {
        "clues": _conn().execute("SELECT COUNT(*) FROM clue").fetchone()[0],
        "clues_understood": _conn().execute(
            "SELECT COUNT(*) FROM clue WHERE state = 'understood'").fetchone()[0],
        "threads_open": _conn().execute(
            "SELECT COUNT(*) FROM thread WHERE state = 'open'").fetchone()[0],
    }
    return jsonify({"areas": areas, "checklist": _checklist_groups(), "counts": counts})


# Group name -> its index in seed.CHECKLIST, i.e. the authored progression
# order (Guardians, Sacred Orbs, Mantras, Maps, Apps) that the Progress screen
# renders in. checklist_item has no group-position column of its own -- there
# is no schema change worth a migration for a database nothing has written to
# yet, per lamulana/db.py's docstring -- so the order is imposed here in
# Python instead of in SQL.
_GROUP_RANK = {name: i for i, (name, _items) in enumerate(_SEED_CHECKLIST)}


def _checklist_groups():
    # ORDER BY group_name here is only the within-group tiebreak's foundation
    # (position, id); the group_name term just keeps rows for the same group
    # adjacent so the loop below can bucket them in one pass. The group order
    # itself is re-imposed by _GROUP_RANK after grouping, below.
    rows = _conn().execute(
        "SELECT id, group_name, name, position, done, done_at, note"
        " FROM checklist_item ORDER BY group_name, position, id"
    ).fetchall()
    by_group = {}
    for r in rows:
        item = dict(r)
        item.pop("group_name")
        item["done"] = bool(item["done"])
        by_group.setdefault(r["group_name"], []).append(item)
    # Seeded groups sort by their authored progression order; a group a player
    # added that isn't in the seed sorts alphabetically after all of them.
    groups = sorted(by_group, key=lambda g: (_GROUP_RANK.get(g, len(_GROUP_RANK)), g))
    return [{"group": g, "items": by_group[g]} for g in groups]
