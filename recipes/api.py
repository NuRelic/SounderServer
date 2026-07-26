"""HTTP surface for the recipes blueprint.

This slice only proves the mount point: the page route and a sections
endpoint. Pantry/recipe/list routes land in later tasks.
"""

import os
import sqlite3
import time

from flask import Blueprint, jsonify, render_template, request, session

from . import db as _db
from .parse import parse_ingredient

bp = Blueprint("recipes", __name__, url_prefix="/recipes")

DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "recipes.db")

CONN = _db.connect(DB_PATH)
_db.init_schema(CONN)
_db.seed_sections(CONN)


def can_edit():
    return bool(session.get("admin") or session.get("can_edit"))


def who():
    """Display name, sent by the frontend from localStorage.ss_name."""
    return (request.args.get("who")
            or (request.get_json(silent=True) or {}).get("who")
            or "someone")


def need_edit():
    """Return an error response if this session may not write, else None."""
    if not can_edit():
        return jsonify({"error": "login required"}), 403
    return None


@bp.route("/")
def page():
    return render_template("recipes.html")


@bp.route("/api/sections")
def api_sections():
    rows = CONN.execute("SELECT id, name, position FROM section ORDER BY position")
    return jsonify({"sections": [dict(r) for r in rows]})


def _unsorted_subsection_id():
    row = CONN.execute("""
        SELECT sub.id FROM subsection sub
        JOIN section s ON s.id = sub.section_id
        WHERE s.name = 'Unsorted'
        LIMIT 1
    """).fetchone()
    return row["id"] if row else None


def _subsection_id_by_name(name):
    if not name:
        return None
    row = CONN.execute(
        "SELECT id FROM subsection WHERE name = ?", (name,)
    ).fetchone()
    return row["id"] if row else None


def _pantry_row(item_id):
    # STORE_ORDER_SQL's ORDER BY lives inside the subquery here; SQLite accepts
    # this (it just becomes a no-op ordering once we filter to a single row by
    # WHERE id = ?), so no restructuring is needed.
    return CONN.execute(f"""
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


def resolve_pantry(name):
    """Find a pantry item by name or alias, case-insensitively. None if absent."""
    if not name:
        return None
    row = CONN.execute(
        "SELECT id FROM pantry_item WHERE name = ?", (name,)
    ).fetchone()
    if row:
        return row["id"]
    row = CONN.execute(
        "SELECT pantry_item_id AS id FROM pantry_alias WHERE alias = ?",
        (name,),
    ).fetchone()
    return row["id"] if row else None


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
            cur = CONN.execute(
                "INSERT INTO pantry_item(name, subsection_id) VALUES(?,?)",
                (name.strip(), subsection_id or _unsorted_subsection_id()),
            )
            CONN.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            CONN.rollback()
    # Lost the race — someone else just created this name. Resolve again
    # outside the lock rather than propagating the collision.
    again = resolve_pantry(name)
    if again:
        return again
    raise


@bp.route("/api/pantry")
def api_pantry_list():
    rows = CONN.execute(_db.STORE_ORDER_SQL).fetchall()
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
            CONN.execute("UPDATE pantry_item SET subsection_id=? WHERE id=?",
                         (_subsection_id_by_name(body["subsection"]), item_id))
            CONN.commit()
    _db.bump_version(CONN)
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
        CONN.execute(f"UPDATE pantry_item SET {', '.join(sets)} WHERE id=?", vals)
        CONN.commit()
    _db.bump_version(CONN)
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
        CONN.execute(
            "INSERT OR IGNORE INTO pantry_alias(pantry_item_id, alias) VALUES(?,?)",
            (item_id, alias),
        )
        CONN.commit()
    return jsonify({"ok": True})


def _store_ingredients(recipe_id, lines):
    """Replace a recipe's ingredients, parsing each line and linking pantry items."""
    with _db.LOCK:
        CONN.execute("DELETE FROM recipe_ingredient WHERE recipe_id=?", (recipe_id,))
        CONN.commit()
    for position, line in enumerate(lines or []):
        parsed = parse_ingredient(line)
        if not parsed:
            continue
        pantry_id = get_or_create_pantry(parsed["name"]) if parsed["name"] else None
        with _db.LOCK:
            CONN.execute("""
                INSERT INTO recipe_ingredient
                    (recipe_id, position, raw_text, qty, unit, pantry_item_id)
                VALUES (?,?,?,?,?,?)
            """, (recipe_id, position, parsed["raw"], parsed["qty"],
                  parsed["unit"], pantry_id))
            CONN.commit()


def _ingredients_json(recipe_id):
    rows = CONN.execute("""
        SELECT ri.*, p.name AS pantry_name, p.is_staple
        FROM recipe_ingredient ri
        LEFT JOIN pantry_item p ON p.id = ri.pantry_item_id
        WHERE ri.recipe_id = ?
        ORDER BY ri.position
    """, (recipe_id,)).fetchall()
    out = []
    for r in rows:
        parsed = parse_ingredient(r["raw_text"]) or {}
        out.append({
            "id": r["id"],
            "raw_text": r["raw_text"],
            "qty": r["qty"],
            "unit": r["unit"],
            "prep": parsed.get("prep", ""),
            "pantry_item_id": r["pantry_item_id"],
            "pantry_name": r["pantry_name"],
            "is_staple": bool(r["is_staple"]),
        })
    return out


def _recipe_json(recipe_id):
    r = CONN.execute("SELECT * FROM recipe WHERE id=?", (recipe_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    out["archived"] = bool(r["archived"])
    out["ingredients"] = _ingredients_json(recipe_id)
    return out


@bp.route("/api/recipes")
def api_recipes_list():
    rows = CONN.execute("""
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
        cur = CONN.execute("""
            INSERT INTO recipe (name, source_name, source_url, servings,
                                time_minutes, instructions, notes, photo_url,
                                created_by, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (name, body.get("source_name"), body.get("source_url"),
              body.get("servings"), body.get("time_minutes"),
              body.get("instructions"), body.get("notes"), body.get("photo_url"),
              who(), int(time.time())))
        CONN.commit()
    recipe_id = cur.lastrowid
    _store_ingredients(recipe_id, body.get("ingredients"))
    _db.bump_version(CONN)
    return jsonify(_recipe_json(recipe_id))


@bp.route("/api/recipes/<int:recipe_id>", methods=["PUT"])
def api_recipe_update(recipe_id):
    gate = need_edit()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    with _db.LOCK:
        CONN.execute("""
            UPDATE recipe SET name=?, source_name=?, source_url=?, servings=?,
                              time_minutes=?, instructions=?, notes=?, photo_url=?
            WHERE id=?
        """, (body.get("name"), body.get("source_name"), body.get("source_url"),
              body.get("servings"), body.get("time_minutes"),
              body.get("instructions"), body.get("notes"), body.get("photo_url"),
              recipe_id))
        CONN.commit()
    if "ingredients" in body:
        _store_ingredients(recipe_id, body["ingredients"])
    _db.bump_version(CONN)
    return jsonify(_recipe_json(recipe_id))


@bp.route("/api/recipes/<int:recipe_id>", methods=["DELETE"])
def api_recipe_archive(recipe_id):
    gate = need_edit()
    if gate:
        return gate
    with _db.LOCK:
        CONN.execute("UPDATE recipe SET archived=1 WHERE id=?", (recipe_id,))
        CONN.commit()
    _db.bump_version(CONN)
    return jsonify({"ok": True})
