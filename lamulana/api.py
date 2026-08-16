"""HTTP surface for the La-Mulana 2 tracker — routes land here in Task 3."""

from flask import Blueprint

bp = Blueprint("lamulana", __name__, url_prefix="/lamulana")
