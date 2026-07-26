"""Turn one written ingredient line into structured parts.

    "2 cloves garlic, minced"
      -> qty=2, unit="clove", name="garlic", prep="minced", raw=<original>

This is best-effort by design. A line it cannot read still becomes a list item
with qty=None — unmerged and unfiled, but present. Never drop a line, and never
guess a quantity that was not written.
"""

import re

from .units import _ALIASES, normalize_unit

_VULGAR = {
    "¼": 0.25, "½": 0.5, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3,
    "⅕": 0.2, "⅖": 0.4, "⅗": 0.6, "⅘": 0.8,
    "⅙": 1 / 6, "⅚": 5 / 6, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}
_VULGAR_CLASS = "".join(_VULGAR)

# Canonical units, derived from the same alias table units.py itself uses —
# a new unit or alias added there is picked up here automatically.
_CANONICAL_UNITS = frozenset(_ALIASES.keys())

# a leading amount: mixed number, ascii fraction, vulgar glyph, decimal (with
# optional thousands separators), optionally the low end of a range
_QTY_RE = re.compile(
    r"^\s*"
    r"(?P<qty>"
    rf"\d+\s*[{_VULGAR_CLASS}]"          # 1½
    r"|\d+\s+\d+\s*/\s*\d+"              # 1 1/2
    r"|\d+\s*/\s*\d+"                    # 1/2
    rf"|[{_VULGAR_CLASS}]"               # ½
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?"     # 1,000 or 12,345.5
    r"|\d+(?:\.\d+)?"                    # 2 or 2.5
    r")"
    r"(?:\s*[-–—]\s*\d+(?:\.\d+)?)?"     # discard the high end of a range
    r"\s*"
)

_PAREN_RE = re.compile(r"\(([^)]*)\)")
_BULLET_RE = re.compile(r"^\s*[-•*]\s*")


def _to_number(text):
    text = text.strip()
    for glyph, value in _VULGAR.items():
        if text.endswith(glyph):
            head = text[: -len(glyph)].strip()
            return (float(head) if head else 0.0) + value
    if "/" in text:
        parts = text.split()
        if len(parts) == 2:                       # "1 1/2"
            num, den = parts[1].split("/")
            return float(parts[0]) + float(num) / float(den)
        num, den = text.split("/")
        return float(num) / float(den)
    return float(text.replace(",", ""))


def _clean_number(value):
    return int(value) if float(value).is_integer() else round(float(value), 4)


def parse_ingredient(line):
    """Parse one line. Returns a dict, or None for a blank line."""
    if not line or not line.strip():
        return None
    raw = line
    work = _BULLET_RE.sub("", raw).strip()

    note_parts = []

    def _capture(match):
        note_parts.append(match.group(1).strip())
        return " "

    work = _PAREN_RE.sub(_capture, work).strip()
    work = re.sub(r"\s+", " ", work)

    qty = None
    match = _QTY_RE.match(work)
    if match:
        try:
            qty = _clean_number(_to_number(match.group("qty")))
            work = work[match.end():].strip()
        except (ValueError, ZeroDivisionError):
            qty = None

    unit = None
    if qty is not None and work:
        tokens = work.split(None, 2)
        # Check the two-word unit "fl oz" before falling back to a single
        # token, since normalizing "fl" alone is not a known unit.
        two_word_unit = None
        if len(tokens) >= 2:
            candidate = normalize_unit(f"{tokens[0]} {tokens[1]}")
            if candidate in _CANONICAL_UNITS:
                two_word_unit = candidate

        if two_word_unit is not None:
            unit = two_word_unit
            work = " ".join(tokens[2:]).strip()
        else:
            parts = work.split(None, 1)
            first_token = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            candidate = normalize_unit(first_token)
            if candidate in _CANONICAL_UNITS:
                unit = candidate
                work = rest.strip()
            else:
                unit = "each"

    name, _, prep = work.partition(",")
    note = "; ".join(p for p in note_parts if p)
    name = name.strip()
    if not name:
        # A line that was nothing but "(a parenthetical)" or a bullet with no
        # text still needs a non-blank row in the list — fall back to
        # whatever we do have rather than showing a blank name.
        name = note or raw.strip()
    return {
        "raw": raw,
        "qty": qty,
        "unit": unit,
        "name": name,
        "prep": prep.strip(),
        "note": note,
    }
