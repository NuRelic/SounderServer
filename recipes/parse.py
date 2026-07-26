"""Turn one written ingredient line into structured parts.

    "2 cloves garlic, minced"
      -> qty=2, unit="clove", name="garlic", prep="minced", raw=<original>

This is best-effort by design. A line it cannot read still becomes a list item
with qty=None — unmerged and unfiled, but present. Never drop a line, and never
guess a quantity that was not written.
"""

import re

from .units import normalize_unit

_VULGAR = {
    "¼": 0.25, "½": 0.5, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3,
    "⅕": 0.2, "⅖": 0.4, "⅗": 0.6, "⅘": 0.8,
    "⅙": 1 / 6, "⅚": 5 / 6, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}
_VULGAR_CLASS = "".join(_VULGAR)

# Canonical units known to recipes.units, spelled out here (not imported) so
# this module stays a pure function of its own regexes plus normalize_unit().
_CANONICAL_UNITS = frozenset({
    "tsp", "tbsp", "floz", "cup", "pint", "quart", "gallon", "ml", "l",
    "g", "kg", "oz", "lb", "each", "clove", "bunch", "can", "jar", "pkg",
    "head", "stalk", "sprig", "slice", "loaf", "box", "bag", "bottle",
    "pinch",
})

# a leading amount: mixed number, ascii fraction, vulgar glyph, or decimal,
# optionally the low end of a range
_QTY_RE = re.compile(
    r"^\s*"
    r"(?P<qty>"
    rf"\d+\s*[{_VULGAR_CLASS}]"          # 1½
    r"|\d+\s+\d+\s*/\s*\d+"              # 1 1/2
    r"|\d+\s*/\s*\d+"                    # 1/2
    rf"|[{_VULGAR_CLASS}]"               # ½
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
    return float(text)


def _clean_number(value):
    return int(value) if float(value).is_integer() else round(float(value), 4)


def parse_ingredient(line):
    """Parse one line. Returns a dict, or None for a blank line."""
    if not line or not line.strip():
        return None
    raw = line.rstrip()
    work = _BULLET_RE.sub("", raw).strip()

    note_parts = []

    def _capture(match):
        note_parts.append(match.group(1).strip())
        return " "

    work = _PAREN_RE.sub(_capture, work).strip()
    work = re.sub(r"\s{2,}", " ", work)

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
        tokens = work.split(" ", 2)
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
            first_token, _, rest = work.partition(" ")
            candidate = normalize_unit(first_token)
            if candidate in _CANONICAL_UNITS:
                unit = candidate
                work = rest.strip()
            else:
                unit = "each"

    name, _, prep = work.partition(",")
    return {
        "raw": raw,
        "qty": qty,
        "unit": unit,
        "name": name.strip(),
        "prep": prep.strip(),
        "note": "; ".join(p for p in note_parts if p),
    }
