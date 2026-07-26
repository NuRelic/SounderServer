"""Unit vocabulary, conversion, and human formatting for the store list.

Two rules drive everything here:

  * Only convert within a family. Volume converts to volume, weight to weight.
    Anything else stacks — "3½ cups + 1 splash" — because a wrong number on
    the list is worse than an ugly one.
  * Count units are each their own family. A clove is not a can is not a
    bunch, so they must never sum together even though they all look like
    counts.
"""

from fractions import Fraction

# canonical unit -> accepted spellings.
# NOTE: the single-letter forms "t" (teaspoon) and "T" (tablespoon) collide
# once lowercased, so they are deliberately left out of this table and are
# instead special-cased (case-sensitively) in normalize_unit() before the
# input is lowercased for everything else.
_ALIASES = {
    "tsp":    ["tsp", "tsps", "teaspoon", "teaspoons"],
    "tbsp":   ["tbsp", "tbsps", "tbs", "tablespoon", "tablespoons"],
    "floz":   ["floz", "fl oz", "fluid ounce", "fluid ounces"],
    "cup":    ["cup", "cups", "c"],
    "pint":   ["pint", "pints", "pt"],
    "quart":  ["quart", "quarts", "qt"],
    "gallon": ["gallon", "gallons", "gal"],
    "ml":     ["ml", "milliliter", "milliliters"],
    "l":      ["l", "liter", "liters", "litre", "litres"],
    "g":      ["g", "gram", "grams"],
    "kg":     ["kg", "kilogram", "kilograms"],
    "oz":     ["oz", "ounce", "ounces"],
    "lb":     ["lb", "lbs", "pound", "pounds"],
    "each":   ["each", "ea", "whole"],
    "clove":  ["clove", "cloves"],
    "bunch":  ["bunch", "bunches"],
    "can":    ["can", "cans"],
    "jar":    ["jar", "jars"],
    "pkg":    ["pkg", "pkgs", "package", "packages", "pack", "packs"],
    "head":   ["head", "heads"],
    "stalk":  ["stalk", "stalks"],
    "sprig":  ["sprig", "sprigs"],
    "slice":  ["slice", "slices"],
    "loaf":   ["loaf", "loaves"],
    "box":    ["box", "boxes"],
    "bag":    ["bag", "bags"],
    "bottle": ["bottle", "bottles"],
    "pinch":  ["pinch", "pinches"],
}

_LOOKUP = {}
for _canon, _spellings in _ALIASES.items():
    for _s in _spellings:
        _LOOKUP[_s.lower().rstrip(".")] = _canon

# within-family conversion factors, expressed in the family's base unit
_VOLUME = {"tsp": 1.0, "tbsp": 3.0, "floz": 6.0, "cup": 48.0, "pint": 96.0,
           "quart": 192.0, "gallon": 768.0, "ml": 0.2028841, "l": 202.8841}
_WEIGHT = {"g": 1.0, "kg": 1000.0, "oz": 28.349523, "lb": 453.59237}

_PLURALS = {"bunch": "bunches", "box": "boxes", "pinch": "pinches",
            "loaf": "loaves", "each": "each", "oz": "oz", "lb": "lb",
            "g": "g", "kg": "kg", "ml": "ml", "l": "l",
            "tsp": "tsp", "tbsp": "tbsp", "floz": "fl oz"}

# Readability threshold for merged volume/weight totals — not a unit-system
# fact, just the point past which a number in this unit stops being something
# a person would want to read off a shopping list (e.g. "771 tsp").
_READABILITY_CAP = 100

_VULGAR = {
    Fraction(1, 4): "¼", Fraction(1, 2): "½", Fraction(3, 4): "¾",
    Fraction(1, 3): "⅓", Fraction(2, 3): "⅔",
    Fraction(1, 8): "⅛", Fraction(3, 8): "⅜",
    Fraction(5, 8): "⅝", Fraction(7, 8): "⅞",
}


def normalize_unit(raw):
    """'Cups' -> 'cup'. Unknown units pass through lowercased; None/'' -> None."""
    if not raw:
        return None
    stripped = str(raw).strip().rstrip(".")
    if not stripped:
        return None
    # Case-sensitive special case: "T" (Tablespoon) and "t" (teaspoon) are
    # both single letters that collide once lowercased. Resolve them here,
    # before anything is lowercased, so both survive.
    if stripped == "T":
        return "tbsp"
    if stripped == "t":
        return "tsp"
    key = stripped.lower()
    return _LOOKUP.get(key, key)


def family_of(unit):
    """Which conversion family a unit belongs to.

    Volume and weight are shared families. Everything else — including
    counts — is its own family, keyed by the unit itself, so cloves never
    sum with cans.
    """
    u = normalize_unit(unit) or "each"
    if u in _VOLUME:
        return "volume"
    if u in _WEIGHT:
        return "weight"
    return "count:" + u


def merge(pairs):
    """Merge [(qty, unit), ...] into the fewest (qty, unit) pairs possible.

    Pairs in the same family are summed. Volume and weight sums are expressed
    in the smallest unit, among those actually present among the merged
    inputs, whose value stays within a readable range (2 cups + 1 quart ->
    6 cups, not 1.5 quarts) — that keeps the result in a unit the recipes
    actually used rather than inventing a "nicer" one. If every present unit
    would render an unreadably large number (3 tsp + 1 gallon), the largest
    present unit is used instead, so the list says "1 gallon" rather than
    "771 tsp". Pairs in different families are never combined; they come
    back untouched.
    """
    buckets = {}
    for qty, unit in pairs:
        if qty is None:
            qty = 0.0
        norm_unit = normalize_unit(unit) or "each"
        fam = family_of(norm_unit)
        buckets.setdefault(fam, []).append((float(qty), norm_unit))

    out = []
    for fam, items in buckets.items():
        if fam == "volume":
            out.append(_merge_convertible(items, _VOLUME))
        elif fam == "weight":
            out.append(_merge_convertible(items, _WEIGHT))
        else:
            total = _tidy(sum(q for q, _ in items))
            out.append((total, items[0][1]))
    return out


def _merge_convertible(items, table):
    """Sum same-family items and render the total in a readable present unit.

    Walk the units actually present, smallest to largest, and take the first
    whose value is at or below the readability cap. If none qualify (every
    present unit would render a huge number), fall back to the largest
    present unit.
    """
    base = sum(q * table[u] for q, u in items)
    present = sorted({u for _, u in items}, key=lambda u: table[u])
    for unit in present:
        value = base / table[unit]
        if value <= _READABILITY_CAP:
            return (_tidy(value), unit)
    largest = present[-1]
    return (_tidy(base / table[largest]), largest)


def _tidy(value):
    """Snap float noise to a clean number: 2.9999999 -> 3, 0.66666 -> 2/3."""
    frac = Fraction(value).limit_denominator(8)
    return int(frac) if frac.denominator == 1 else float(frac)


def format_quantity(qty, unit):
    """'3½ cups'. Fractions render as vulgar glyphs; units pluralize."""
    unit = normalize_unit(unit) or "each"
    frac = Fraction(qty).limit_denominator(8)
    whole, rest = divmod(frac, 1)
    glyph = _VULGAR.get(rest, "")

    if glyph and whole:
        number = f"{int(whole)}{glyph}"
    elif glyph:
        number = glyph
    elif rest:
        # Not a fraction we have a glyph for (denominator not in _VULGAR).
        number = f"{float(frac):g}"
    else:
        number = str(int(whole))

    if unit == "each":
        return number

    # Only pluralize once we're past a single whole unit — "½ clove" stays
    # singular, but "2 cups" and "3½ cups" both need the plural form. English
    # also pluralizes at zero ("0 cups"), so that's the one exception below 1.
    value = float(frac)
    plural = value > 1 or value == 0
    label = _PLURALS.get(unit, unit + "s") if plural else unit
    return f"{number} {label}"
