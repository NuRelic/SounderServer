"""HTTP surface for the recipes blueprint.

The page route, sections, pantry, recipes, and the store list. Read it in that
order — the list routes at the bottom are the ones with real behavior behind
them (merging claims onto shared lines, grouping into the store walk).
"""

import os
import sqlite3
import time

from flask import Blueprint, jsonify, render_template, request

from auth import can_edit, need_edit

from . import db as _db
from . import units
from .parse import parse_ingredient

bp = Blueprint("recipes", __name__, url_prefix="/recipes")

DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "recipes.db")


def _conn():
    """This request thread's database connection.

    Not a module-level `CONN` object: one connection shared across waitress's
    16 threads corrupts concurrent reads of the same query (see
    `db.get_conn`). Every call site below goes through this instead, which is
    a dict lookup on a `threading.local` — cheap enough to do per statement.
    """
    return _db.get_conn(DB_PATH)


# Schema and seed run once, here at import, on the importing thread — not
# per-thread inside `_conn()`. Both are idempotent, but doing them on every
# thread's first request would mean 16 redundant `executescript` calls
# contending for the write lock during the first seconds of a deploy.
_db.init_schema(_conn())
_db.seed_sections(_conn())


def who():
    """Display name, sent by the frontend from localStorage.ss_name."""
    return (request.args.get("who")
            or (request.get_json(silent=True) or {}).get("who")
            or "someone")


@bp.route("/")
def page():
    return render_template("recipes.html")


@bp.route("/api/sections")
def api_sections():
    """The store layout: sections in walking order, each with its sub-categories.

    `subsections` is here so the pantry filing screen can offer the real list
    rather than a hardcoded copy of seed.py — that list is editable in the
    database and a frontend copy would silently drift the first time it is.
    """
    rows = _conn().execute(
        "SELECT id, name, position FROM section ORDER BY position"
    ).fetchall()
    subs = _conn().execute(
        "SELECT section_id, name FROM subsection ORDER BY section_id, position"
    ).fetchall()
    by_section = {}
    for s in subs:
        by_section.setdefault(s["section_id"], []).append(s["name"])
    out = []
    for r in rows:
        section = dict(r)
        section["subsections"] = by_section.get(r["id"], [])
        out.append(section)
    return jsonify({"sections": out})


def _unsorted_subsection_id():
    row = _conn().execute("""
        SELECT sub.id FROM subsection sub
        JOIN section s ON s.id = sub.section_id
        WHERE s.name = 'Unsorted'
        LIMIT 1
    """).fetchone()
    return row["id"] if row else None


def _subsection_id_by_name(name):
    if not name:
        return None
    row = _conn().execute(
        "SELECT id FROM subsection WHERE name = ?", (name,)
    ).fetchone()
    return row["id"] if row else None


def _pantry_row(item_id):
    # STORE_ORDER_SQL's ORDER BY lives inside the subquery here; SQLite accepts
    # this (it just becomes a no-op ordering once we filter to a single row by
    # WHERE id = ?), so no restructuring is needed.
    return _conn().execute(f"""
        SELECT * FROM ({_db.STORE_ORDER_SQL}) WHERE id = ?
    """, (item_id,)).fetchone()


def _pantry_json(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "section_name": row["section_name"] or "Unsorted",
        "subsection_name": row["subsection_name"],
        "is_staple": bool(row["is_staple"]),
        "buy_unit": row["buy_unit"],
        "shaws_url": row["shaws_url"],
        "shaws_sku": row["shaws_sku"],
        "notes": row["notes"],
    }


# Anything shorter than this is never produced or consumed as a plural variant.
# "bas" and "ba" are not words we want colliding with "bass" and "bay".
_MIN_STEM = 3

# The endings where English adds -es rather than -s. Used in both directions:
# going singular -> plural we add -es after them, and going plural -> singular
# a word ending in <one of these> + "es" gives up the whole "es", never just
# the "s". That second half is what keeps "molasses" off "molasse".
_ES_ENDINGS = ("s", "x", "z", "ch", "sh", "o")


def _plural_variants(name):
    """Dumb, predictable singular/plural spellings of `name`.

    Deliberately not a stemmer. It exists for one case: an ingredient line says
    "1 onion, diced" and the pantry already has "Onions", or says "2 tomatoes"
    and the pantry has "Tomato". Splitting those into two rows breaks quantity
    merging on the most ordinary input there is.

    Anything it does not recognise -- irregulars, latinate plurals -- simply
    yields no variant and the name lands in Unsorted as its own item, which is
    the same outcome as before this function existed. Over-matching is the
    expensive failure (two unrelated groceries collapsed into one line), so the
    rules stay conservative: no bare trailing "s" is ever stripped from a word
    ending "ss", and no variant shorter than three characters is offered.
    """
    n = (name or "").strip()
    if len(n) < _MIN_STEM:
        return []
    low = n.lower()
    out = []

    def add(v):
        if len(v) >= _MIN_STEM and v.lower() != low and v not in out:
            out.append(v)

    # singular -> plural
    if low.endswith("y") and len(low) > 2 and low[-2] not in "aeiou":
        add(n[:-1] + "ies")                      # berry -> berries
    elif low.endswith(_ES_ENDINGS):
        add(n + "es")                            # tomato -> tomatoes, box -> boxes
        if low.endswith("o"):
            add(n + "s")                         # avocado -> avocados
    else:
        add(n + "s")                             # onion -> onions

    # plural -> singular
    if low.endswith("ies") and len(low) > 4:
        add(n[:-3] + "y")                        # berries -> berry
    elif low.endswith("es") and any(
            low[:-2].endswith(e) for e in _ES_ENDINGS):
        add(n[:-2])                              # tomatoes -> tomato, glasses -> glass
    elif low.endswith("s") and not low.endswith("ss"):
        add(n[:-1])                              # onions -> onion

    return out


def _lookup_exact(name):
    row = _conn().execute(
        "SELECT id FROM pantry_item WHERE name = ?", (name,)
    ).fetchone()
    if row:
        return row["id"]
    row = _conn().execute(
        "SELECT pantry_item_id AS id FROM pantry_alias WHERE alias = ?",
        (name,),
    ).fetchone()
    return row["id"] if row else None


def resolve_pantry(name):
    """Find a pantry item by name or alias, case-insensitively. None if absent.

    Falls back to a simple singular/plural spelling of the same name (see
    `_plural_variants`) so "onion" and "onions" are one grocery thing. An exact
    hit always wins: a pantry that genuinely holds both spellings keeps them.
    """
    if not name:
        return None
    hit = _lookup_exact(name)
    if hit:
        return hit
    for variant in _plural_variants(name):
        hit = _lookup_exact(variant)
        if hit:
            return hit
    return None


def get_or_create_pantry(name, subsection_id=None):
    """Resolve a name to a pantry item, creating it in Unsorted if new.

    This is what makes filing permanent: an ingredient we have never seen gets a
    real pantry row immediately, so the one tap that files it is remembered.

    `pantry_item.name` is COLLATE NOCASE UNIQUE, so a case-variant duplicate
    (two requests racing to create "onions" and "Onions") can lose the
    resolve-then-insert race and hit an IntegrityError on the INSERT. Treat
    that the same as finding it already there: re-resolve rather than letting
    the error propagate.
    """
    existing = resolve_pantry(name)
    if existing:
        return existing
    with _db.LOCK:
        try:
            cur = _conn().execute(
                "INSERT INTO pantry_item(name, subsection_id) VALUES(?,?)",
                (name.strip(), subsection_id or _unsorted_subsection_id()),
            )
            _conn().commit()
            return cur.lastrowid
        except sqlite3.IntegrityError as collision:
            _conn().rollback()
            lost_race = collision
    # Lost the race — someone else just created this name. Resolve again
    # outside the lock rather than propagating the collision.
    again = resolve_pantry(name)
    if again:
        return again
    # The name collided but still doesn't resolve, so the constraint we tripped
    # was not the one we assumed. Re-raise the original error explicitly: a bare
    # `raise` here has no active exception (the except block has already ended)
    # and would surface as a RuntimeError masking the real cause.
    raise lost_race


@bp.route("/api/pantry")
def api_pantry_list():
    rows = _conn().execute(_db.STORE_ORDER_SQL).fetchall()
    return jsonify({"items": [_pantry_json(r) for r in rows]})


@bp.route("/api/pantry/resolve")
def api_pantry_resolve():
    item_id = resolve_pantry(request.args.get("name", ""))
    if not item_id:
        return jsonify({"id": None}), 404
    return jsonify(_pantry_json(_pantry_row(item_id)))


@bp.route("/api/pantry", methods=["POST"])
def api_pantry_create():
    gate = need_edit()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    item_id = get_or_create_pantry(name, _subsection_id_by_name(body.get("subsection")))
    if body.get("subsection"):
        with _db.LOCK:
            _conn().execute("UPDATE pantry_item SET subsection_id=? WHERE id=?",
                            (_subsection_id_by_name(body["subsection"]), item_id))
            _conn().commit()
    _db.bump_version(_conn())
    return jsonify(_pantry_json(_pantry_row(item_id)))


_PANTRY_FIELDS = ("is_staple", "buy_unit", "shaws_url", "shaws_sku", "notes", "name")


@bp.route("/api/pantry/<int:item_id>", methods=["PATCH"])
def api_pantry_update(item_id):
    gate = need_edit()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    sets, vals = [], []
    for field in _PANTRY_FIELDS:
        if field in body:
            sets.append(f"{field}=?")
            value = body[field]
            vals.append(int(bool(value)) if field == "is_staple" else value)
    if "subsection" in body:
        sets.append("subsection_id=?")
        vals.append(_subsection_id_by_name(body["subsection"]))
    if not sets:
        return jsonify({"error": "nothing to update"}), 400
    vals.append(item_id)
    with _db.LOCK:
        _conn().execute(f"UPDATE pantry_item SET {', '.join(sets)} WHERE id=?", vals)
        _conn().commit()
    _db.bump_version(_conn())
    return jsonify(_pantry_json(_pantry_row(item_id)))


@bp.route("/api/pantry/<int:item_id>/alias", methods=["POST"])
def api_pantry_alias(item_id):
    gate = need_edit()
    if gate:
        return gate
    alias = ((request.get_json(silent=True) or {}).get("alias") or "").strip()
    if not alias:
        return jsonify({"error": "alias required"}), 400
    with _db.LOCK:
        _conn().execute(
            "INSERT OR IGNORE INTO pantry_alias(pantry_item_id, alias) VALUES(?,?)",
            (item_id, alias),
        )
        _conn().commit()
    return jsonify({"ok": True})


def _store_ingredients(recipe_id, lines):
    """Replace a recipe's ingredients, parsing each line and linking pantry items."""
    with _db.LOCK:
        _conn().execute("DELETE FROM recipe_ingredient WHERE recipe_id=?", (recipe_id,))
        _conn().commit()
    for position, line in enumerate(lines or []):
        parsed = parse_ingredient(line)
        if not parsed:
            continue
        pantry_id = get_or_create_pantry(parsed["name"]) if parsed["name"] else None
        with _db.LOCK:
            _conn().execute("""
                INSERT INTO recipe_ingredient
                    (recipe_id, position, raw_text, qty, unit, prep, pantry_item_id)
                VALUES (?,?,?,?,?,?,?)
            """, (recipe_id, position, parsed["raw"], parsed["qty"],
                  parsed["unit"], parsed["prep"], pantry_id))
            _conn().commit()


def _ingredients_json(recipe_id):
    rows = _conn().execute("""
        SELECT ri.*, p.name AS pantry_name, p.is_staple
        FROM recipe_ingredient ri
        LEFT JOIN pantry_item p ON p.id = ri.pantry_item_id
        WHERE ri.recipe_id = ?
        ORDER BY ri.position
    """, (recipe_id,)).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "raw_text": r["raw_text"],
            "qty": r["qty"],
            "unit": r["unit"],
            # Read what was parsed at save time. Re-parsing here would make a
            # later change to parse.py rewrite recipes nobody touched.
            "prep": r["prep"] or "",
            "pantry_item_id": r["pantry_item_id"],
            "pantry_name": r["pantry_name"],
            "is_staple": bool(r["is_staple"]),
        })
    return out


def _recipe_json(recipe_id):
    r = _conn().execute("SELECT * FROM recipe WHERE id=?", (recipe_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    out["archived"] = bool(r["archived"])
    out["ingredients"] = _ingredients_json(recipe_id)
    return out


@bp.route("/api/recipes")
def api_recipes_list():
    rows = _conn().execute("""
        SELECT id, name, source_name, source_url, servings, time_minutes,
               photo_url, created_by
        FROM recipe WHERE archived = 0
        ORDER BY name COLLATE NOCASE
    """).fetchall()
    return jsonify({"recipes": [dict(r) for r in rows]})


@bp.route("/api/recipes/<int:recipe_id>")
def api_recipe_detail(recipe_id):
    data = _recipe_json(recipe_id)
    if not data:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@bp.route("/api/recipes", methods=["POST"])
def api_recipe_create():
    gate = need_edit()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    with _db.LOCK:
        cur = _conn().execute("""
            INSERT INTO recipe (name, source_name, source_url, servings,
                                time_minutes, instructions, notes, photo_url,
                                created_by, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (name, body.get("source_name"), body.get("source_url"),
              body.get("servings"), body.get("time_minutes"),
              body.get("instructions"), body.get("notes"), body.get("photo_url"),
              who(), int(time.time())))
        _conn().commit()
    recipe_id = cur.lastrowid
    _store_ingredients(recipe_id, body.get("ingredients"))
    _db.bump_version(_conn())
    return jsonify(_recipe_json(recipe_id))


@bp.route("/api/recipes/<int:recipe_id>", methods=["PUT"])
def api_recipe_update(recipe_id):
    gate = need_edit()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    with _db.LOCK:
        _conn().execute("""
            UPDATE recipe SET name=?, source_name=?, source_url=?, servings=?,
                              time_minutes=?, instructions=?, notes=?, photo_url=?
            WHERE id=?
        """, (body.get("name"), body.get("source_name"), body.get("source_url"),
              body.get("servings"), body.get("time_minutes"),
              body.get("instructions"), body.get("notes"), body.get("photo_url"),
              recipe_id))
        _conn().commit()
    if "ingredients" in body:
        _store_ingredients(recipe_id, body["ingredients"])
    _db.bump_version(_conn())
    return jsonify(_recipe_json(recipe_id))


@bp.route("/api/recipes/<int:recipe_id>", methods=["DELETE"])
def api_recipe_archive(recipe_id):
    gate = need_edit()
    if gate:
        return gate
    with _db.LOCK:
        _conn().execute("UPDATE recipe SET archived=1 WHERE id=?", (recipe_id,))
        _conn().commit()
    _db.bump_version(_conn())
    return jsonify({"ok": True})


def _find_or_make_line(pantry_item_id=None, free_text=None):
    """One line per grocery thing. Unchecked lines merge; checked ones do not.

    A checked line is already in the cart, so a new claim on the same item needs
    its own line rather than silently reviving something you already bought.

    The SELECT and the INSERT both sit inside the lock. Splitting them is a
    check-then-act race: two phones adding onions at the same moment would both
    see no open line and both insert one. Task 1's partial unique index catches
    that at the schema level, so the lock is what keeps it from surfacing as an
    IntegrityError under normal use.
    """
    with _db.LOCK:
        if pantry_item_id:
            row = _conn().execute(
                "SELECT id FROM list_line WHERE pantry_item_id=? AND checked=0",
                (pantry_item_id,),
            ).fetchone()
        else:
            row = _conn().execute(
                "SELECT id FROM list_line WHERE free_text=? AND checked=0",
                (free_text,),
            ).fetchone()
        if row:
            return row["id"]
        cur = _conn().execute(
            "INSERT INTO list_line(pantry_item_id, free_text, created_at)"
            " VALUES(?,?,?)",
            (pantry_item_id, free_text, int(time.time())),
        )
        _conn().commit()
        return cur.lastrowid


def _list_json():
    """The store list, merged and grouped into the walking order."""
    rows = _conn().execute("""
        SELECT ll.id, ll.free_text, ll.checked, ll.checked_by,
               p.id AS pantry_id, p.name AS pantry_name, p.buy_unit, p.shaws_url,
               sub.name AS subsection_name,
               COALESCE(s.name, 'Unsorted')  AS section_name,
               COALESCE(s.position, 9999)    AS section_pos,
               COALESCE(sub.position, 9999)  AS sub_pos
        FROM list_line ll
        LEFT JOIN pantry_item p ON p.id = ll.pantry_item_id
        LEFT JOIN subsection sub ON sub.id = p.subsection_id
        LEFT JOIN section s ON s.id = sub.section_id
        ORDER BY section_pos, sub_pos,
                 COALESCE(p.name, ll.free_text) COLLATE NOCASE
    """).fetchall()

    lines, by_section = [], {}
    for r in rows:
        contribs = _conn().execute("""
            SELECT c.qty, c.unit, c.added_by, rc.name AS recipe_name
            FROM list_contribution c
            LEFT JOIN recipe rc ON rc.id = c.recipe_id
            WHERE c.list_line_id = ?
        """, (r["id"],)).fetchall()

        merged = units.merge([(c["qty"], c["unit"]) for c in contribs
                              if c["qty"] is not None])
        qty_display = " + ".join(units.format_quantity(q, u) for q, u in merged)

        sources = []
        for c in contribs:
            sources.append(c["recipe_name"] if c["recipe_name"]
                           else f"added by {c['added_by']}")

        line = {
            "id": r["id"],
            "name": r["pantry_name"] or r["free_text"],
            "pantry_id": r["pantry_id"],
            "qty_display": qty_display,
            "buy_unit": r["buy_unit"],
            "shaws_url": r["shaws_url"],
            "sources": sorted(set(sources)),
            "checked": bool(r["checked"]),
            "checked_by": r["checked_by"],
            "section_name": r["section_name"],
        }
        lines.append(line)
        by_section.setdefault(r["section_name"], []).append(line)

    section_rows = _conn().execute(
        "SELECT name FROM section ORDER BY position"
    ).fetchall()
    sections = [{"name": s["name"], "lines": by_section.get(s["name"], [])}
                for s in section_rows]

    meals = _conn().execute("""
        SELECT r.id, r.name FROM meal_plan m
        JOIN recipe r ON r.id = m.recipe_id
        ORDER BY m.added_at
    """).fetchall()

    return {
        "version": _db.get_version(_conn()),
        "lines": lines,
        "sections": sections,
        "meals": [dict(m) for m in meals],
    }


@bp.route("/api/list")
def api_list():
    return jsonify(_list_json())


@bp.route("/api/list/add-recipe/<int:recipe_id>", methods=["POST"])
def api_list_add_recipe(recipe_id):
    gate = need_edit()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    skip = set(body.get("skip") or [])
    person = who()
    rows = _conn().execute(
        "SELECT * FROM recipe_ingredient WHERE recipe_id=? ORDER BY position",
        (recipe_id,),
    ).fetchall()
    for ing in rows:
        if ing["id"] in skip:
            continue
        line_id = _find_or_make_line(
            pantry_item_id=ing["pantry_item_id"],
            free_text=None if ing["pantry_item_id"] else ing["raw_text"])
        with _db.LOCK:
            _conn().execute("""
                INSERT INTO list_contribution
                    (list_line_id, recipe_id, added_by, qty, unit, raw_text)
                VALUES (?,?,?,?,?,?)
            """, (line_id, recipe_id, person, ing["qty"], ing["unit"], ing["raw_text"]))
            _conn().commit()
    with _db.LOCK:
        _conn().execute(
            "INSERT OR IGNORE INTO meal_plan(recipe_id, added_by, added_at)"
            " VALUES(?,?,?)",
            (recipe_id, person, int(time.time())),
        )
        _conn().commit()
    _db.bump_version(_conn())
    return jsonify(_list_json())


@bp.route("/api/list/add", methods=["POST"])
def api_list_add_free():
    gate = need_edit()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    person = who()
    pantry_id = resolve_pantry(name)      # link if we already know it, else free text
    line_id = _find_or_make_line(pantry_item_id=pantry_id,
                                 free_text=None if pantry_id else name)
    with _db.LOCK:
        _conn().execute("""
            INSERT INTO list_contribution
                (list_line_id, recipe_id, added_by, qty, unit, raw_text)
            VALUES (?, NULL, ?, ?, ?, ?)
        """, (line_id, person, body.get("qty"), body.get("unit"), name))
        _conn().commit()
    _db.bump_version(_conn())
    return jsonify(_list_json())


@bp.route("/api/list/line/<int:line_id>/check", methods=["POST"])
def api_list_check(line_id):
    gate = need_edit()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    checked = bool(body.get("checked"))
    with _db.LOCK:
        _conn().execute(
            "UPDATE list_line SET checked=?, checked_by=?, checked_at=? WHERE id=?",
            (int(checked), who() if checked else None,
             int(time.time()) if checked else None, line_id),
        )
        _conn().commit()
    _db.bump_version(_conn())
    return jsonify(_list_json())


@bp.route("/api/list/finish-trip", methods=["POST"])
def api_list_finish_trip():
    gate = need_edit()
    if gate:
        return gate
    with _db.LOCK:
        _conn().execute("DELETE FROM list_line WHERE checked=1")
        _conn().commit()
    _db.bump_version(_conn())
    return jsonify(_list_json())


@bp.route("/api/list/remove-recipe/<int:recipe_id>", methods=["POST"])
def api_list_remove_recipe(recipe_id):
    """Withdraw one recipe's claims from the list, leaving other recipes' intact.

    Deleting only this recipe's `list_contribution` rows is what makes a line
    shared by several recipes drop by just this recipe's share rather than
    disappearing or losing nothing. Orphan cleanup (a line with zero
    contributions left) is handled by `trg_drop_childless_line` in db.py, which
    fires per-row on this DELETE — no separate sweep needed here.
    """
    gate = need_edit()
    if gate:
        return gate
    with _db.LOCK:
        _conn().execute("DELETE FROM list_contribution WHERE recipe_id=?", (recipe_id,))
        _conn().execute("DELETE FROM meal_plan WHERE recipe_id=?", (recipe_id,))
        _conn().commit()
    _db.bump_version(_conn())
    return jsonify(_list_json())


@bp.route("/api/list/poll")
def api_list_poll():
    """Cheap change check. Returns the full list only when the version moved."""
    try:
        since = int(request.args.get("since", -1))
    except ValueError:
        since = -1
    current = _db.get_version(_conn())
    if since == current:
        return jsonify({"changed": False, "version": current})
    payload = _list_json()
    payload["changed"] = True
    return jsonify(payload)


@bp.route("/api/list/export")
def api_list_export():
    """Machine-readable unchecked list, for filling a Shaws pickup cart.

    `needs_product_link` is the useful half: those are the items where somebody
    has to pick a product once, after which this export gets more automatic.
    """
    data = _list_json()
    items, missing = [], []
    for line in data["lines"]:
        if line["checked"]:
            continue
        items.append({
            "name": line["name"],
            "qty_display": line["qty_display"],
            "buy_unit": line["buy_unit"],
            "shaws_url": line["shaws_url"],
            "section": line["section_name"],
            "wanted_by": line["sources"],
        })
        if not line["shaws_url"]:
            missing.append(line["name"])
    return jsonify({"items": items, "needs_product_link": missing})
