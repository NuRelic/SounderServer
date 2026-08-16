"""La-Mulana 2 tracker — a blueprint inside the SounderServer app.

Mounted at /lamulana. Owns its own SQLite database and shares nothing with the
soundboard or the recipes blueprint except the Flask session, which is what
gates writes.
"""

from .api import bp as lamulana_bp

__all__ = ["lamulana_bp"]
