"""HTTP surface for the recipes blueprint.

This slice only proves the mount point: the page route and a sections
endpoint. Pantry/recipe/list routes land in later tasks.
"""

import os

from flask import Blueprint, jsonify, render_template, request, session

from . import db as _db

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
