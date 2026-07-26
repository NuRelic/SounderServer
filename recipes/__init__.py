"""Family recipes + store list — a blueprint inside the SounderServer app.

Mounted at /recipes. Owns its own SQLite database and shares nothing with the
soundboard except the Flask session (for edit rights) and the display name the
frontend keeps in localStorage.
"""

from .api import bp as recipes_bp

__all__ = ["recipes_bp"]
