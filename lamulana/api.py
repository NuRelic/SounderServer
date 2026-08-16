"""HTTP surface for the La-Mulana 2 tracker.

Read it in order: helpers, then clues, then threads, then the link between them,
then search and the checklist. The thread routes are the ones with real behavior
-- solving a thread reaches back into the clues that fed it.

Clue CRUD lands in Task 4. Thread CRUD and the detail view that inlines
clues land in Task 5. The link between them and search are still ahead in
Tasks 6-8 -- if you came here looking for those, they are not written yet.
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

# Validate request bodies for the clue routes and the thread routes below.
# Must match the CHECK constraints on clue.state / clue.source in
# lamulana/db.py's SCHEMA -- adding a value to one without the other means
# either a legal value 400s here or an illegal one reaches SQLite and 500s
# on the CHECK, depending on which side you forgot.
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
    data = request.get_json(silent=True)
    # A JSON array or bare string parses fine and then dies on .get; every
    # route below assumes an object, so anything else is an empty body.
    return data if isinstance(data, dict) else {}


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

# Deliberately stops after the FROM/JOIN, with no WHERE: callers append their
# own WHERE onto this (never the other way around -- a WHERE cannot precede a
# JOIN), so list and single-row lookups share one FROM clause instead of two
# copies that could drift apart. Task 5's thread routes follow the same shape.
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


# Field specs for _clean_body(), one per writable table. A spec value is a
# type or tuple of types checked with isinstance() (include type(None) for a
# nullable column), a set of literal values for an enum column -- kept as a
# set rather than reusing CLUE_STATES/CLUE_SOURCES's tuples directly so
# _clean_body can tell "isinstance-me" specs from "membership-check-me"
# specs apart by Python type alone -- or the string "area" for a nullable
# foreign key into the `area` table.
CLUE_FIELDS = {
    "title": str,
    "body": str,
    "room": (str, type(None)),
    "interpretation": (str, type(None)),
    "area_id": "area",
    "state": set(CLUE_STATES),
    "source": set(CLUE_SOURCES),
}

# Read by the thread create/patch routes below. Defined here, next to
# CLUE_FIELDS, so the two field lists stay side by side for whoever adds a
# column to either table.
THREAD_FIELDS = {
    "title": str,
    "body": (str, type(None)),
    "solution": (str, type(None)),
    "area_id": "area",
}


def _clean_body(body, fields):
    """Validate and normalize a JSON body against a field spec (see e.g. CLUE_FIELDS).

    Returns `(clean_body, None)` on success, or `(None, (response, 400))` on
    the first field that fails -- `response` already names the field, so
    callers just write `b, err = _clean_body(_body(), FIELDS); if err: return err`.

    This is the one place a wrong JSON type or a dangling foreign key gets
    caught, so every route that writes a table gets the same protection
    against reaching SQLite as a 500: `.strip()` raising on a non-string
    title, a NOT NULL IntegrityError on a null body, a FOREIGN KEY
    IntegrityError on a dangling area_id, or a ProgrammingError from binding
    a list/dict, or a wrong-but-truthy area_id like `True` silently binding
    as SQLite integer 1 (`bool` is an `int` subclass).

    Returns a new dict rather than mutating `body` in place: PATCH builds its
    SQL params straight from what this returns (`list(clean.values())`), so
    the normalization below has to be something every caller is actually
    handed, not a side effect that a reordered or short-circuited check
    could silently skip.

    An empty string normalizes to None wherever None is an accepted value for
    that field -- an empty form field means "cleared", the same signal
    whether the field is a nullable id or a nullable text column -- and is
    left alone otherwise. That keeps `body: ""` (a string-only, non-nullable
    field: "empty body" is a real, intentional state) as the empty string it
    is, rather than becoming a null this function would then reject.

    Unknown keys in `body` are silently dropped: callers select what they
    write from the return value (`for f in clean`), never from the raw
    request body, so an extra key here would be inert either way.
    """
    clean = {}
    for name, spec in fields.items():
        if name not in body:
            continue
        v = body[name]
        nullable = spec == "area" or (isinstance(spec, tuple) and type(None) in spec)
        if v == "" and nullable:
            v = None
        if spec == "area":
            if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
                return None, (jsonify({"error": f"{name} must be an integer or null"}), 400)
            if v is not None and not _conn().execute(
                    "SELECT 1 FROM area WHERE id = ?", (v,)).fetchone():
                return None, (jsonify({"error": f"no such area for {name}"}), 400)
        elif isinstance(spec, set):
            if v not in spec:
                return None, (jsonify({"error": f"{name} must be one of {sorted(spec)}"}), 400)
        else:
            types = spec if isinstance(spec, tuple) else (spec,)
            if not isinstance(v, types):
                return None, (jsonify({"error": f"{name} has the wrong type"}), 400)
        clean[name] = v
    return clean, None


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
    b, err = _clean_body(_body(), CLUE_FIELDS)
    if err:
        return err
    title = b.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    state = b.get("state") or "raw"
    source = b.get("source") or "tablet"
    now = _now()
    with _db.LOCK:
        cur = _conn().execute("""
            INSERT INTO clue (title, body, area_id, room, source, interpretation,
                              state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, b.get("body", ""), b.get("area_id"), b.get("room"),
              source, b.get("interpretation"), state, now, now))
        _conn().commit()
    return jsonify({"clue": _one_clue(cur.lastrowid)})


@bp.route("/api/clues/<int:clue_id>", methods=["PATCH"])
def api_clue_patch(clue_id):
    if (err := need_edit()):
        return err
    b, err = _clean_body(_body(), CLUE_FIELDS)
    if err:
        return err
    if "title" in b and not b["title"].strip():
        return jsonify({"error": "title required"}), 400
    if not b:
        return jsonify({"error": "nothing to change"}), 400
    sets = ", ".join(f"{f} = ?" for f in b)
    params = list(b.values()) + [_now(), clue_id]
    # The existence check and the write are the same locked UPDATE (rowcount
    # tells them apart) rather than a SELECT before the lock followed by the
    # UPDATE inside it: a concurrent DELETE landing in that gap would have
    # left this returning 200 with a clue that no longer exists.
    with _db.LOCK:
        cur = _conn().execute(f"UPDATE clue SET {sets}, updated_at = ? WHERE id = ?", params)
        _conn().commit()
    if not cur.rowcount:
        return jsonify({"error": "no such clue"}), 404
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


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------

THREAD_SELECT = """
    SELECT t.id, t.title, t.area_id, a.name AS area, t.body, t.state, t.solution,
           t.created_at, t.updated_at, t.solved_at,
           (SELECT COUNT(*) FROM clue_thread ct WHERE ct.thread_id = t.id)
               AS clue_count
    FROM thread t LEFT JOIN area a ON a.id = t.area_id
"""


def _one_thread(thread_id):
    row = _conn().execute(THREAD_SELECT + " WHERE t.id = ?", (thread_id,)).fetchone()
    return dict(row) if row else None


@bp.route("/api/threads")
def api_threads():
    where, params = [], []
    if request.args.get("area"):
        where.append("t.area_id = ?"); params.append(request.args["area"])
    if request.args.get("state"):
        where.append("t.state = ?"); params.append(request.args["state"])
    clause, qp = _search_terms(request.args.get("q"),
                               ["t.title", "t.body", "t.solution"])
    if clause:
        where.append(clause); params += qp
    sql = THREAD_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Open threads first: this list is a worklist, and a solved thread is
    # history. Within each half, most recently touched first.
    sql += " ORDER BY t.state = 'solved', t.updated_at DESC, t.id DESC"
    return jsonify({"threads": [dict(r) for r in _conn().execute(sql, params).fetchall()]})


@bp.route("/api/threads/<int:thread_id>")
def api_thread_detail(thread_id):
    """One thread with every linked clue inlined at full length.

    The clues are the point: this is the screen you sit down with when you
    finally try to crack something, and it exists so the scattered text is on
    one page instead of in three browser tabs.
    """
    thread = _one_thread(thread_id)
    if not thread:
        return jsonify({"error": "no such thread"}), 404
    # Explicit CASE, not `ORDER BY c.state`: alphabetical order of
    # raw/understood/used happens to match lifecycle order today, but that's
    # a coincidence a renamed or added state would silently break.
    rows = _conn().execute(CLUE_SELECT + """
        JOIN clue_thread ct ON ct.clue_id = c.id
        WHERE ct.thread_id = ?
        ORDER BY CASE c.state WHEN 'raw' THEN 0 WHEN 'understood' THEN 1
                              WHEN 'used' THEN 2 ELSE 3 END, c.id
    """, (thread_id,)).fetchall()
    thread["clues"] = _clue_json(rows)
    return jsonify({"thread": thread})


@bp.route("/api/threads", methods=["POST"])
def api_thread_create():
    if (err := need_edit()):
        return err
    # THREAD_FIELDS has no "state": a thread is always born open, so create
    # has nothing to branch on the way PATCH does with solved_at -- unlike
    # PATCH, there is no raw-body "state" check here, and a "state" key in
    # the POST body is silently ignored rather than 400ing.
    b, err = _clean_body(_body(), THREAD_FIELDS)
    if err:
        return err
    title = b.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    now = _now()
    with _db.LOCK:
        cur = _conn().execute("""
            INSERT INTO thread (title, area_id, body, solution, state, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'open', ?, ?)
        """, (title, b.get("area_id"), b.get("body"), b.get("solution"), now, now))
        _conn().commit()
    return jsonify({"thread": _one_thread(cur.lastrowid)})


@bp.route("/api/threads/<int:thread_id>", methods=["PATCH"])
def api_thread_patch(thread_id):
    if (err := need_edit()):
        return err
    # THREAD_FIELDS deliberately has no "state" entry: a state change carries
    # the solved_at bookkeeping below, which is not a plain column write, so
    # it stays a hand-written check against the raw body rather than folding
    # into _clean_body. _clean_body still validates and normalizes every
    # other field -- title, area_id, body, solution -- the same way clues do.
    raw = _body()
    b, err = _clean_body(raw, THREAD_FIELDS)
    if err:
        return err
    if "state" in raw:
        if raw["state"] not in THREAD_STATES:
            return jsonify({"error": f"state must be one of {sorted(THREAD_STATES)}"}), 400
        b["state"] = raw["state"]
    if "title" in b and not b["title"].strip():
        return jsonify({"error": "title required"}), 400
    if not b:
        return jsonify({"error": "nothing to change"}), 400
    now = _now()
    sets = ", ".join(f"{f} = ?" for f in b)
    params = list(b.values()) + [now, thread_id]
    # The existence check and the write are the same locked UPDATE (rowcount
    # tells them apart) rather than a SELECT before the lock followed by the
    # UPDATE inside it: a concurrent DELETE landing in that gap would have
    # left this returning 200 with a thread that no longer exists -- the same
    # fix Task 4 applied to clue PATCH. That closes the gap for the write
    # itself; the _one_thread() re-read below still happens after the lock is
    # released, so a DELETE landing in that narrower window can still turn a
    # successful PATCH into a 200 with a null thread -- same as clue PATCH.
    with _db.LOCK:
        cur = _conn().execute(f"UPDATE thread SET {sets}, updated_at = ? WHERE id = ?", params)
        if cur.rowcount:
            if b.get("state") == "solved":
                _conn().execute(
                    "UPDATE thread SET solved_at = ? WHERE id = ? AND solved_at IS NULL",
                    (now, thread_id))
            if b.get("state") == "open":
                _conn().execute("UPDATE thread SET solved_at = NULL WHERE id = ?",
                                (thread_id,))
        _conn().commit()
    if not cur.rowcount:
        return jsonify({"error": "no such thread"}), 404
    return jsonify({"thread": _one_thread(thread_id)})


@bp.route("/api/threads/<int:thread_id>", methods=["DELETE"])
def api_thread_delete(thread_id):
    if (err := need_edit()):
        return err
    with _db.LOCK:
        _conn().execute("DELETE FROM thread WHERE id = ?", (thread_id,))
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
