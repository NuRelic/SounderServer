"""Who may write. One definition, imported by the soundboard and every blueprint.

Deliberately not three copies: this is the predicate that decides whether a
request may change state, and a copy that drifts from the others is a hole
rather than an inconsistency. Listening and reading stay open to everyone --
these gate writes only.
"""
from flask import jsonify, session


def can_edit():
    return bool(session.get("admin") or session.get("can_edit"))


def need_edit():
    """An error response if this session may not write, else None."""
    if not can_edit():
        return jsonify({"error": "login required"}), 403
    return None
