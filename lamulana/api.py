"""HTTP surface for the La-Mulana 2 tracker.

Read it in order: helpers, then clues, then threads, then the link between them,
then search and the checklist. The thread routes are the ones with real behavior
-- solving a thread reaches back into the clues that fed it.

Clue CRUD lands in Task 4. Threads, the link between them, and search are
still ahead in Tasks 5-8 -- if you came here looking for them, they are not
written yet.
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

# Validate request bodies for the clue routes below and the thread routes
# still to come in Task 5.
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


# ---------------------------------------------------------------------------
# Clues
# ---------------------------------------------------------------------------

CLUE_SELECT = """
    SELECT c.id, c.title, c.body, c.area_id, a.name AS area, c.room, c.source,
           c.interpretation, c.state, c.created_at, c.updated_at
    FROM clue c LEFT JOIN area a ON a.id = c.area_id
"""


def _like_escape(word):
    """Treat %, _ and \\ as literal characters in a search box, not wildcards.

    Someone typing "100%" means the string, not "100 followed by anything".
    """
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_terms(q, columns):
    """(sql_clause, params) ANDing every word in `q` across `columns`.

    Plain LIKE rather than FTS5: a playthrough produces a few hundred rows, so
    the scan is instant, it does not depend on how the host's SQLite was
    compiled, and there is no index to drift out of sync with the table.
    """
    words = [w for w in (q or "").split() if w]
    if not words:
        return "", []
    blob = " || ' ' || ".join(f"COALESCE({c}, '')" for c in columns)
    clause = " AND ".join([f"{blob} LIKE ? ESCAPE '\\'"] * len(words))
    return clause, [f"%{_like_escape(w)}%" for w in words]


def _threads_for_clues(ids):
    """{clue_id: [{id, title, state}]} for the given clue ids, in one query."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = _conn().execute(f"""
        SELECT ct.clue_id, t.id, t.title, t.state
        FROM clue_thread ct JOIN thread t ON t.id = ct.thread_id
        WHERE ct.clue_id IN ({marks})
        ORDER BY t.title
    """, ids).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["clue_id"], []).append(
            {"id": r["id"], "title": r["title"], "state": r["state"]})
    return out


def _clue_json(rows):
    clues = [dict(r) for r in rows]
    links = _threads_for_clues([c["id"] for c in clues])
    for c in clues:
        c["threads"] = links.get(c["id"], [])
    return clues


def _one_clue(clue_id):
    row = _conn().execute(CLUE_SELECT + " WHERE c.id = ?", (clue_id,)).fetchone()
    return _clue_json([row])[0] if row else None


def _clue_field_error(b):
    """(jsonify, 400) if `b` has a bad title/body/room/interpretation/area_id, else None.

    Runs before create/patch touch the database, so a wrong JSON type comes
    back as a 400 here instead of surfacing later as `.strip()` raising on a
    non-string title, or SQLite raising a NOT NULL/FOREIGN KEY
    IntegrityError for a null body or a dangling area_id -- both would
    otherwise reach the caller as an unhandled 500. Shared by
    api_clue_create and api_clue_patch. Mutates `b["area_id"]` from "" to
    None in place -- an empty <select> means "no area", not an error -- so
    callers can use b.get("area_id") directly afterward.
    """
    for field in ("title", "body"):
        if field in b and not isinstance(b[field], str):
            return jsonify({"error": f"{field} must be a string"}), 400
    for field in ("room", "interpretation"):
        if field in b and b[field] is not None and not isinstance(b[field], str):
            return jsonify({"error": f"{field} must be a string or null"}), 400
    if "area_id" in b:
        if b["area_id"] == "":
            b["area_id"] = None
        if b["area_id"] is not None and not _conn().execute(
                "SELECT 1 FROM area WHERE id = ?", (b["area_id"],)).fetchone():
            return jsonify({"error": "no such area"}), 400
    return None


@bp.route("/api/clues")
def api_clues():
    where, params = [], []
    if request.args.get("area"):
        where.append("c.area_id = ?"); params.append(request.args["area"])
    if request.args.get("state"):
        where.append("c.state = ?"); params.append(request.args["state"])
    if request.args.get("source"):
        where.append("c.source = ?"); params.append(request.args["source"])
    clause, qp = _search_terms(request.args.get("q"),
                               ["c.title", "c.body", "c.interpretation", "c.room"])
    if clause:
        where.append(clause); params += qp
    sql = CLUE_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.updated_at DESC, c.id DESC"
    return jsonify({"clues": _clue_json(_conn().execute(sql, params).fetchall())})


@bp.route("/api/clues", methods=["POST"])
def api_clue_create():
    if (err := need_edit()):
        return err
    b = _body()
    if (err := _clue_field_error(b)):
        return err
    title = (b.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    state = b.get("state") or "raw"
    source = b.get("source") or "tablet"
    if state not in CLUE_STATES or source not in CLUE_SOURCES:
        return jsonify({"error": "bad state or source"}), 400
    now = _now()
    with _db.LOCK:
        cur = _conn().execute("""
            INSERT INTO clue (title, body, area_id, room, source, interpretation,
                              state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, b.get("body") or "", b.get("area_id"), b.get("room"),
              source, b.get("interpretation"), state, now, now))
        _conn().commit()
    return jsonify({"clue": _one_clue(cur.lastrowid)})


CLUE_PATCHABLE = ("title", "body", "area_id", "room", "source",
                  "interpretation", "state")


@bp.route("/api/clues/<int:clue_id>", methods=["PATCH"])
def api_clue_patch(clue_id):
    if (err := need_edit()):
        return err
    b = _body()
    if (err := _clue_field_error(b)):
        return err
    if "state" in b and b["state"] not in CLUE_STATES:
        return jsonify({"error": "bad state"}), 400
    if "source" in b and b["source"] not in CLUE_SOURCES:
        return jsonify({"error": "bad source"}), 400
    if "title" in b and not (b.get("title") or "").strip():
        return jsonify({"error": "title required"}), 400
    fields = [k for k in CLUE_PATCHABLE if k in b]
    if not fields:
        return jsonify({"error": "nothing to change"}), 400
    if not _one_clue(clue_id):
        return jsonify({"error": "no such clue"}), 404
    sets = ", ".join(f"{f} = ?" for f in fields)
    params = [b[f] for f in fields] + [_now(), clue_id]
    with _db.LOCK:
        _conn().execute(f"UPDATE clue SET {sets}, updated_at = ? WHERE id = ?", params)
        _conn().commit()
    return jsonify({"clue": _one_clue(clue_id)})


@bp.route("/api/clues/<int:clue_id>", methods=["DELETE"])
def api_clue_delete(clue_id):
    if (err := need_edit()):
        return err
    # Deliberately 200s even if clue_id never existed, unlike PATCH's 404:
    # delete is idempotent and the frontend only cares that the row is gone
    # afterward, not whether this call was the one that removed it.
    with _db.LOCK:
        _conn().execute("DELETE FROM clue WHERE id = ?", (clue_id,))
        _conn().commit()
    return jsonify({"ok": True})


@bp.route("/api/rooms")
def api_rooms():
    """Distinct room names, for the capture form's autocomplete."""
    rows = _conn().execute(
        "SELECT DISTINCT room FROM clue WHERE room IS NOT NULL AND room != ''"
        " ORDER BY room"
    ).fetchall()
    return jsonify({"rooms": [r["room"] for r in rows]})
