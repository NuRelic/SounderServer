# Family Recipes → Store List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A phone-first recipe collection and a shared, live-syncing shopping checklist ordered to match how the household walks their Shaws.

**Architecture:** A Flask blueprint mounted at `/recipes` inside the existing SounderServer app, backed by its own `data/recipes.db` SQLite database. `server.py` changes by two lines (import + `register_blueprint`). Recipe code never opens `server.py`; soundboard code never opens `recipes/`. The frontend is a single `templates/recipes.html` page with three tabs, polling for cross-phone sync the same way the soundboard already does.

**Tech Stack:** Python 3, Flask 3, SQLite (stdlib `sqlite3`), pytest, vanilla JS (no build step, no framework — matching `templates/index.html`).

**Spec:** `docs/superpowers/specs/2026-07-26-family-recipes-store-list-design.md`

---

## Before you start

Read the spec. The decisions in it were argued through and each records the
alternatives rejected — do not "improve" on them without going back to the user.

Three things that trip people up on this codebase:

1. **Tests re-import the server module.** `tests/conftest.py` deletes
   `sys.modules["server"]` and re-imports it under a `tmp_path` `DATA_DIR`. Any
   module-level database connection must therefore be created at import time
   from an env-var-driven path, exactly like `CATALOG_DB` in `server.py:229-231`.
   A connection cached across tests will leak state between them.
2. **There is uncommitted work in `server.py` and `templates/index.html`.** It
   belongs to the user and is unrelated to this feature. Do not stage it, do not
   revert it, do not rebase over it.
3. **Four people write concurrently.** Every write goes through a lock and the
   database runs in WAL mode. Never hold a transaction open across a request.

Work on the `recipes` branch. Commit after every task.

## File Structure

**Created:**

| File | Responsibility |
|------|----------------|
| `recipes/__init__.py` | Blueprint factory. Assembles the blueprint from `api.py` and exposes `recipes_bp`. Nothing else. |
| `recipes/db.py` | Connection, schema DDL, migrations, section/sub-category seed, version counter. The only module that writes SQL DDL. |
| `recipes/units.py` | Unit vocabulary, conversion families, quantity merging, human formatting. Pure functions, no I/O. |
| `recipes/parse.py` | One ingredient line → `qty` / `unit` / `name` / `prep`. Pure functions, no I/O. |
| `recipes/api.py` | All HTTP routes. Thin — validation and orchestration only; logic lives in the modules above. |
| `recipes/seed.py` | The five sections and their sub-categories as data. Imported by `db.py`. |
| `templates/recipes.html` | The whole frontend. Three tabs, vanilla JS. |
| `tests/test_recipes_units.py` | Conversion and merge behavior. |
| `tests/test_recipes_parse.py` | Ingredient line parsing. |
| `tests/test_recipes_db.py` | Schema, seeding, sort ordering. |
| `tests/test_recipes_api.py` | Route-level lifecycle tests. |

**Modified:**

| File | Change |
|------|--------|
| `server.py` | Two lines: import `recipes_bp`, register it at `/recipes`. |
| `tests/conftest.py` | Add a `recipes_db` fixture and a `recipes_client` fixture. |

`api.py` is the file most likely to sprawl. If it passes ~400 lines, split it
into `api_recipes.py`, `api_list.py`, and `api_pantry.py`, all registered on the
same blueprint. Do not let it become a second `server.py`.

---

## Task 1: Database module — schema, seed, and ordering

The foundation. Everything else reads from these tables, so the sort key and the
`list_line`/`list_contribution` split must be right before anything is built on
top of them.

**Files:**
- Create: `recipes/seed.py`
- Create: `recipes/db.py`
- Create: `tests/test_recipes_db.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write the section seed data**

Create `recipes/seed.py`. This is data, not logic — the order of the lists *is*
the walking order of the store.

```python
"""Default store layout — the order of these lists is the order we walk Shaws.

Sections are visible headers. Sub-categories are invisible sort keys: they exist
so items land in the right part of a section without adding headers to scroll
past. Both are reorderable in the app; this is a first-run seed, not a constant.
"""

SECTIONS = [
    ("Produce & Fancy Cheese", [
        "produce", "fancy cheese",
    ]),
    ("Early Aisles", [
        "shelf-stable fruit", "coffee & tea", "cereal", "breakfast",
        "spices", "baking",
    ]),
    ("Middle Aisles", [
        "plant meat", "broths", "soups", "box dinners", "pasta",
        "pasta sauce", "canned veg", "rice", "asian", "mexican",
        "chips", "cookies", "salty snacks",
    ]),
    ("Late Aisles", [
        "candy", "canned teas", "paper", "home", "medicine", "toiletries",
        "soda", "drinks", "seltzer", "wine", "beer", "liquor",
    ]),
    ("Freezer / Dairy / Bread", [
        "freezer", "dairy", "bread",
    ]),
    ("Unsorted", [
        "unsorted",
    ]),
]
```

- [ ] **Step 2: Write the failing schema test**

Create `tests/test_recipes_db.py`:

```python
import recipes.db as db


def test_seeds_six_sections_in_walking_order(recipes_db):
    rows = recipes_db.execute(
        "SELECT name FROM section ORDER BY position"
    ).fetchall()
    assert [r["name"] for r in rows] == [
        "Produce & Fancy Cheese",
        "Early Aisles",
        "Middle Aisles",
        "Late Aisles",
        "Freezer / Dairy / Bread",
        "Unsorted",
    ]


def test_unsorted_is_always_last(recipes_db):
    positions = dict(recipes_db.execute(
        "SELECT name, position FROM section"
    ).fetchall())
    assert positions["Unsorted"] == max(positions.values())


def test_subsections_keep_their_declared_order(recipes_db):
    rows = recipes_db.execute("""
        SELECT sub.name FROM subsection sub
        JOIN section s ON s.id = sub.section_id
        WHERE s.name = 'Produce & Fancy Cheese'
        ORDER BY sub.position
    """).fetchall()
    assert [r["name"] for r in rows] == ["produce", "fancy cheese"]


def test_seeding_is_idempotent(recipes_db):
    before = recipes_db.execute("SELECT COUNT(*) c FROM section").fetchone()["c"]
    db.seed_sections(recipes_db)
    after = recipes_db.execute("SELECT COUNT(*) c FROM section").fetchone()["c"]
    assert before == after
```

- [ ] **Step 3: Add the test fixtures**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def recipes_db(tmp_path, monkeypatch):
    """A fresh, seeded recipes database on disk, isolated per test."""
    import importlib, sys
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    for mod in ("recipes.db", "recipes.api", "recipes", "recipes.seed"):
        sys.modules.pop(mod, None)
    db = importlib.import_module("recipes.db")
    conn = db.connect(str(tmp_path / "recipes.db"))
    db.init_schema(conn)
    db.seed_sections(conn)
    return conn
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_recipes_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'recipes'`

- [ ] **Step 5: Write the schema**

Create `recipes/db.py`:

```python
"""SQLite storage for the recipes + store list feature.

Connection lifecycle mirrors the soundboard's catalog DB (server.py:229): one
module-level connection opened at import from a DATA_DIR-driven path, guarded by
a lock, with check_same_thread=False because Waitress serves on many threads.
WAL is on because four phones write concurrently.
"""

import os
import sqlite3
import threading

from .seed import SECTIONS

LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS section (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subsection (
    id         INTEGER PRIMARY KEY,
    section_id INTEGER NOT NULL REFERENCES section(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    position   INTEGER NOT NULL,
    UNIQUE(section_id, name)
);

CREATE TABLE IF NOT EXISTS pantry_item (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    subsection_id  INTEGER REFERENCES subsection(id) ON DELETE SET NULL,
    is_staple      INTEGER NOT NULL DEFAULT 0,
    buy_unit       TEXT,
    shaws_url      TEXT,
    shaws_sku      TEXT,
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS pantry_alias (
    pantry_item_id INTEGER NOT NULL REFERENCES pantry_item(id) ON DELETE CASCADE,
    alias          TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS recipe (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    source_name  TEXT,
    source_url   TEXT,
    servings     INTEGER,
    time_minutes INTEGER,
    instructions TEXT,
    notes        TEXT,
    photo_url    TEXT,
    created_by   TEXT,
    created_at   INTEGER NOT NULL,
    archived     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recipe_ingredient (
    id             INTEGER PRIMARY KEY,
    recipe_id      INTEGER NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    raw_text       TEXT NOT NULL,
    qty            REAL,
    unit           TEXT,
    pantry_item_id INTEGER REFERENCES pantry_item(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS list_line (
    id             INTEGER PRIMARY KEY,
    pantry_item_id INTEGER REFERENCES pantry_item(id) ON DELETE SET NULL,
    free_text      TEXT,
    checked        INTEGER NOT NULL DEFAULT 0,
    checked_by     TEXT,
    checked_at     INTEGER,
    created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS list_contribution (
    id           INTEGER PRIMARY KEY,
    list_line_id INTEGER NOT NULL REFERENCES list_line(id) ON DELETE CASCADE,
    recipe_id    INTEGER REFERENCES recipe(id) ON DELETE CASCADE,
    added_by     TEXT,
    qty          REAL,
    unit         TEXT,
    raw_text     TEXT
);

CREATE TABLE IF NOT EXISTS meal_plan (
    recipe_id INTEGER PRIMARY KEY REFERENCES recipe(id) ON DELETE CASCADE,
    added_by  TEXT,
    added_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ing_recipe   ON recipe_ingredient(recipe_id);
CREATE INDEX IF NOT EXISTS idx_ing_pantry   ON recipe_ingredient(pantry_item_id);
CREATE INDEX IF NOT EXISTS idx_contrib_line ON list_contribution(list_line_id);
CREATE INDEX IF NOT EXISTS idx_contrib_rcp  ON list_contribution(recipe_id);
CREATE INDEX IF NOT EXISTS idx_line_pantry  ON list_line(pantry_item_id);
"""


def connect(path):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn):
    with LOCK:
        conn.executescript(SCHEMA)
        conn.commit()


def seed_sections(conn):
    """Insert the default store layout. Idempotent — safe on every boot."""
    with LOCK:
        for s_pos, (section_name, subs) in enumerate(SECTIONS):
            conn.execute(
                "INSERT OR IGNORE INTO section(name, position) VALUES(?,?)",
                (section_name, s_pos),
            )
            sid = conn.execute(
                "SELECT id FROM section WHERE name=?", (section_name,)
            ).fetchone()["id"]
            for sub_pos, sub_name in enumerate(subs):
                conn.execute(
                    "INSERT OR IGNORE INTO subsection(section_id, name, position)"
                    " VALUES(?,?,?)",
                    (sid, sub_name, sub_pos),
                )
        conn.commit()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_recipes_db.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 7: Write the failing test for the sort key**

The whole store-order feature reduces to this one ordering. Append to
`tests/test_recipes_db.py`:

```python
def _mk_item(conn, name, section, sub):
    sub_id = conn.execute("""
        SELECT sub.id FROM subsection sub JOIN section s ON s.id = sub.section_id
        WHERE s.name=? AND sub.name=?
    """, (section, sub)).fetchone()["id"]
    conn.execute(
        "INSERT INTO pantry_item(name, subsection_id) VALUES(?,?)", (name, sub_id)
    )
    conn.commit()


def test_store_order_is_section_then_subsection_then_alphabetical(recipes_db):
    # deliberately inserted out of order
    _mk_item(recipes_db, "Trash bags",  "Late Aisles",             "home")
    _mk_item(recipes_db, "Gruyere",     "Produce & Fancy Cheese",  "fancy cheese")
    _mk_item(recipes_db, "Cumin",       "Early Aisles",            "spices")
    _mk_item(recipes_db, "Apples",      "Produce & Fancy Cheese",  "produce")
    _mk_item(recipes_db, "Candy bars",  "Late Aisles",             "candy")
    _mk_item(recipes_db, "Coffee",      "Early Aisles",            "coffee & tea")
    _mk_item(recipes_db, "Bananas",     "Produce & Fancy Cheese",  "produce")

    rows = recipes_db.execute(db.STORE_ORDER_SQL).fetchall()

    assert [r["name"] for r in rows] == [
        "Apples", "Bananas",   # produce, alphabetical
        "Gruyere",             # fancy cheese comes after produce
        "Coffee",              # coffee & tea precedes spices
        "Cumin",
        "Candy bars",          # candy precedes home
        "Trash bags",
    ]


def test_unfiled_items_sort_last(recipes_db):
    _mk_item(recipes_db, "Apples", "Produce & Fancy Cheese", "produce")
    recipes_db.execute("INSERT INTO pantry_item(name) VALUES('Mystery powder')")
    recipes_db.commit()
    rows = recipes_db.execute(db.STORE_ORDER_SQL).fetchall()
    assert rows[-1]["name"] == "Mystery powder"
```

- [ ] **Step 8: Run it to verify it fails**

Run: `./.venv/bin/pytest tests/test_recipes_db.py -k store_order -v`
Expected: FAIL — `AttributeError: module 'recipes.db' has no attribute 'STORE_ORDER_SQL'`

- [ ] **Step 9: Add the ordering query**

Append to `recipes/db.py`. `LEFT JOIN` plus `COALESCE` to a sentinel is what puts
unfiled items last without a second query.

```python
# The store walk, as one ordering: section position, then the invisible
# sub-category position, then alphabetical. Unfiled items (no subsection) sort
# to the very end via the COALESCE sentinel.
STORE_ORDER_SQL = """
SELECT p.*, s.name AS section_name, s.id AS section_id, sub.name AS subsection_name
FROM pantry_item p
LEFT JOIN subsection sub ON sub.id = p.subsection_id
LEFT JOIN section    s   ON s.id  = sub.section_id
ORDER BY COALESCE(s.position, 9999),
         COALESCE(sub.position, 9999),
         p.name COLLATE NOCASE
"""
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_recipes_db.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 11: Add the version counter**

Cross-phone sync needs a monotonic counter that every write bumps. Append to
`recipes/db.py`:

```python
def bump_version(conn):
    """Bump the list version. Every mutating request must call this."""
    conn.execute("""
        INSERT INTO meta(key, value) VALUES('list_version', '1')
        ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1
    """)
    conn.commit()
    return get_version(conn)


def get_version(conn):
    row = conn.execute(
        "SELECT value FROM meta WHERE key='list_version'"
    ).fetchone()
    return int(row["value"]) if row else 0
```

And the test, appended to `tests/test_recipes_db.py`:

```python
def test_version_starts_at_zero_and_increases(recipes_db):
    assert db.get_version(recipes_db) == 0
    assert db.bump_version(recipes_db) == 1
    assert db.bump_version(recipes_db) == 2
    assert db.get_version(recipes_db) == 2
```

- [ ] **Step 12: Run the full file**

Run: `./.venv/bin/pytest tests/test_recipes_db.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 13: Commit**

```bash
git add recipes/db.py recipes/seed.py tests/test_recipes_db.py tests/conftest.py
git commit -m "feat(recipes): schema, store-layout seed, and store ordering"
```

---

## Task 2: Unit conversion and quantity merging

Pure functions, no I/O. This is what turns "chili wants 1 onion, stir fry wants
2" into `3`, and what stops the list from telling you to buy pints of milk.

**Files:**
- Create: `recipes/units.py`
- Create: `tests/test_recipes_units.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_recipes_units.py`. Note the third group: units that *cannot*
merge must stack rather than guess. That is a correctness requirement, not a
nicety — silently converting `1 splash` into cups would put wrong numbers on the
list.

```python
import pytest
from recipes import units


@pytest.mark.parametrize("raw,expected", [
    ("cups", "cup"), ("Cup", "cup"), ("c", "cup"),
    ("tablespoons", "tbsp"), ("Tbsp.", "tbsp"), ("T", "tbsp"),
    ("teaspoon", "tsp"), ("t", "tsp"),
    ("ounces", "oz"), ("oz.", "oz"), ("lbs", "lb"), ("pounds", "lb"),
    ("grams", "g"), ("kilogram", "kg"),
    ("cloves", "clove"), ("bunches", "bunch"), ("cans", "can"),
    ("packages", "pkg"), ("pkgs", "pkg"),
    ("", None), (None, None),
])
def test_normalize_unit(raw, expected):
    assert units.normalize_unit(raw) == expected


def test_volume_units_share_a_family():
    assert units.family_of("cup") == units.family_of("tbsp") == "volume"


def test_count_units_are_each_their_own_family():
    # a clove is not a can; they must never sum together
    assert units.family_of("clove") != units.family_of("can")


def test_merges_same_unit():
    assert units.merge([(1, "each"), (2, "each")]) == [(3, "each")]


def test_merges_across_a_volume_family():
    # 2 cups + 1 quart = 6 cups
    assert units.merge([(2, "cup"), (1, "quart")]) == [(6, "cup")]


def test_merges_weight():
    assert units.merge([(8, "oz"), (1, "lb")]) == [(24, "oz")]


def test_refuses_to_merge_across_families_and_stacks_instead():
    out = units.merge([(2, "cup"), (1, "splash")])
    assert sorted(out) == sorted([(2, "cup"), (1, "splash")])


def test_bare_quantities_count_as_each():
    assert units.merge([(1, None), (2, None)]) == [(3, "each")]


@pytest.mark.parametrize("qty,unit,expected", [
    (3, "each", "3"),
    (1, "cup", "1 cup"),
    (2, "cup", "2 cups"),
    (3.5, "cup", "3½ cups"),
    (0.25, "tsp", "¼ tsp"),
    (1.75, "lb", "1¾ lb"),
    (0.5, "clove", "½ clove"),
    (2, "bunch", "2 bunches"),
])
def test_format_quantity_is_human_readable(qty, unit, expected):
    assert units.format_quantity(qty, unit) == expected


def test_format_avoids_float_noise():
    # 1/3 cup + 1/3 cup must not render as "0.6666666666666666 cups"
    merged = units.merge([(1 / 3, "cup"), (1 / 3, "cup")])
    assert units.format_quantity(*merged[0]) == "⅔ cup"
```

- [ ] **Step 2: Run to verify they fail**

Run: `./.venv/bin/pytest tests/test_recipes_units.py -v`
Expected: FAIL — `ImportError: cannot import name 'units'`

- [ ] **Step 3: Implement the unit module**

Create `recipes/units.py`:

```python
"""Unit vocabulary, conversion, and human formatting for the store list.

Two rules drive everything here:

  * Only convert within a family. Volume converts to volume, weight to weight.
    Anything else stacks — "3½ cups + 1 splash" — because a wrong number on the
    list is worse than an ugly one.
  * Count units are each their own family. A clove is not a can is not a bunch,
    so they must never sum together even though they all look like counts.
"""

from fractions import Fraction

# canonical unit -> accepted spellings
_ALIASES = {
    "tsp":    ["tsp", "tsps", "t", "teaspoon", "teaspoons"],
    "tbsp":   ["tbsp", "tbsps", "tbs", "T", "tablespoon", "tablespoons"],
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

# preferred display unit per family, largest first — the first one that yields
# a value >= 1 wins, so 3.5 cups stays cups instead of becoming 0.22 gallons
_VOLUME_PREF = ["gallon", "quart", "cup", "tbsp", "tsp"]
_WEIGHT_PREF = ["lb", "oz"]

_PLURALS = {"bunch": "bunches", "box": "boxes", "pinch": "pinches",
            "loaf": "loaves", "each": "each", "oz": "oz", "lb": "lb",
            "g": "g", "kg": "kg", "ml": "ml", "l": "l",
            "tsp": "tsp", "tbsp": "tbsp", "floz": "fl oz"}

_VULGAR = {
    Fraction(1, 4): "¼", Fraction(1, 2): "½", Fraction(3, 4): "¾",
    Fraction(1, 3): "⅓", Fraction(2, 3): "⅔",
    Fraction(1, 8): "⅛", Fraction(3, 8): "⅜",
    Fraction(5, 8): "⅝", Fraction(7, 8): "⅞",
}


def normalize_unit(raw):
    """'Cups' -> 'cup'. Unknown units pass through lowercased; None stays None."""
    if not raw:
        return None
    key = str(raw).strip().lower().rstrip(".")
    if not key:
        return None
    return _LOOKUP.get(key, key)


def family_of(unit):
    """Which conversion family a unit belongs to.

    Volume and weight are shared families. Everything else — including counts —
    is its own family, keyed by the unit itself, so cloves never sum with cans.
    """
    u = normalize_unit(unit) or "each"
    if u in _VOLUME:
        return "volume"
    if u in _WEIGHT:
        return "weight"
    return "count:" + u


def merge(pairs):
    """Merge [(qty, unit), ...] into the fewest (qty, unit) pairs possible.

    Pairs in the same family are summed and rendered in that family's most
    readable unit. Pairs in different families come back untouched.
    """
    buckets = {}
    for qty, unit in pairs:
        if qty is None:
            qty = 0.0
        fam = family_of(unit)
        buckets.setdefault(fam, []).append((float(qty), normalize_unit(unit) or "each"))

    out = []
    for fam, items in buckets.items():
        if fam == "volume":
            base = sum(q * _VOLUME[u] for q, u in items)
            out.append(_render(base, _VOLUME, _VOLUME_PREF))
        elif fam == "weight":
            base = sum(q * _WEIGHT[u] for q, u in items)
            out.append(_render(base, _WEIGHT, _WEIGHT_PREF))
        else:
            out.append((_tidy(sum(q for q, _ in items)), items[0][1]))
    return out


def _render(base, table, preference):
    for unit in preference:
        value = base / table[unit]
        if value >= 1:
            return (_tidy(value), unit)
    smallest = preference[-1]
    return (_tidy(base / table[smallest]), smallest)


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
    if rest and not glyph:
        number = f"{float(frac):g}"
    elif whole and glyph:
        number = f"{int(whole)}{glyph}"
    elif glyph:
        number = glyph
    else:
        number = str(int(whole))

    if unit == "each":
        return number
    plural = float(frac) != 1
    label = _PLURALS.get(unit, unit + "s") if plural else unit
    return f"{number} {label}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_recipes_units.py -v`
Expected: PASS, all parametrized cases.

If `test_merges_across_a_volume_family` fails with `[(6.0, 'cup')]` vs
`[(6, 'cup')]`, `_tidy` is not collapsing the float — fix `_tidy`, not the test.

- [ ] **Step 5: Commit**

```bash
git add recipes/units.py tests/test_recipes_units.py
git commit -m "feat(recipes): unit families, quantity merging, human formatting"
```

---

## Task 3: Ingredient line parsing

The single fiddliest component, which is why it lives alone behind a pure
function with heavy tests. It is **allowed to fail**: an unparseable line still
becomes a list item, just an unmerged one. Parse quality affects convenience,
never correctness — do not add speculative cleverness here.

**Files:**
- Create: `recipes/parse.py`
- Create: `tests/test_recipes_parse.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_recipes_parse.py`:

```python
import pytest
from recipes.parse import parse_ingredient


def test_simple_count():
    r = parse_ingredient("2 yellow onions")
    assert (r["qty"], r["unit"], r["name"]) == (2, "each", "yellow onions")


def test_unit_and_name():
    r = parse_ingredient("3 cups vegetable broth")
    assert (r["qty"], r["unit"], r["name"]) == (3, "cup", "vegetable broth")


def test_prep_after_comma_is_split_off():
    r = parse_ingredient("2 cloves garlic, minced")
    assert r["qty"] == 2
    assert r["unit"] == "clove"
    assert r["name"] == "garlic"
    assert r["prep"] == "minced"


def test_ascii_fraction():
    r = parse_ingredient("1/2 cup milk")
    assert r["qty"] == 0.5 and r["unit"] == "cup" and r["name"] == "milk"


def test_mixed_ascii_fraction():
    r = parse_ingredient("1 1/2 cups flour")
    assert r["qty"] == 1.5 and r["unit"] == "cup"


def test_unicode_vulgar_fraction():
    r = parse_ingredient("½ tsp salt")
    assert r["qty"] == 0.5 and r["unit"] == "tsp"


def test_mixed_unicode_fraction():
    r = parse_ingredient("1½ cups rice")
    assert r["qty"] == 1.5 and r["unit"] == "cup"


def test_range_takes_the_low_end():
    # buy for the smaller amount; you can always grab another
    r = parse_ingredient("2-3 tablespoons olive oil")
    assert r["qty"] == 2 and r["unit"] == "tbsp"


def test_en_dash_range():
    r = parse_ingredient("2–3 lbs potatoes")
    assert r["qty"] == 2 and r["unit"] == "lb"


def test_parenthetical_goes_to_note_not_name():
    r = parse_ingredient("1 can (14 oz) crushed tomatoes")
    assert r["qty"] == 1 and r["unit"] == "can"
    assert r["name"] == "crushed tomatoes"
    assert "14 oz" in r["note"]


def test_no_quantity_at_all():
    r = parse_ingredient("Salt and pepper to taste")
    assert r["qty"] is None
    assert r["unit"] is None
    assert r["name"] == "Salt and pepper to taste"


def test_raw_text_is_always_preserved_verbatim():
    line = "2 cloves garlic, minced"
    assert parse_ingredient(line)["raw"] == line


def test_leading_bullet_is_stripped():
    r = parse_ingredient("- 2 cups water")
    assert r["qty"] == 2 and r["name"] == "water"


def test_unparseable_garbage_still_returns_a_usable_item():
    r = parse_ingredient("a handful of whatever")
    assert r["qty"] is None
    assert r["name"] == "a handful of whatever"
    assert r["raw"] == "a handful of whatever"


def test_empty_line_returns_none():
    assert parse_ingredient("   ") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `./.venv/bin/pytest tests/test_recipes_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'recipes.parse'`

- [ ] **Step 3: Implement the parser**

Create `recipes/parse.py`:

```python
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
_BULLET_RE = re.compile(r"^\s*[-•*•]\s*")


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
        first, _, rest = work.partition(" ")
        candidate = normalize_unit(first)
        # only treat it as a unit if it is one we actually know
        if candidate and candidate != first.lower().rstrip("."):
            unit = candidate
            work = rest.strip()
        elif first.lower().rstrip(".") in _KNOWN:
            unit = normalize_unit(first)
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


# units whose canonical form equals their written form ("cup" -> "cup"), which
# the alias-changed check above would otherwise miss
_KNOWN = {
    "tsp", "tbsp", "cup", "pint", "quart", "gallon", "ml", "l", "g", "kg",
    "oz", "lb", "each", "clove", "bunch", "can", "jar", "pkg", "head",
    "stalk", "sprig", "slice", "loaf", "box", "bag", "bottle", "pinch",
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_recipes_parse.py -v`
Expected: PASS, 15 tests.

`test_parenthetical_goes_to_note_not_name` is the one most likely to fail first —
the paren must be stripped *before* the quantity regex runs, or `1 can (14 oz)`
leaves `(14 oz)` glued to the name.

- [ ] **Step 5: Commit**

```bash
git add recipes/parse.py tests/test_recipes_parse.py
git commit -m "feat(recipes): best-effort ingredient line parser"
```

---

## Task 4: Blueprint wiring

Two lines in `server.py` and a page that returns 200. Smallest possible slice
that proves the mount point works before any real routes exist.

**Files:**
- Create: `recipes/__init__.py`
- Create: `recipes/api.py`
- Create: `templates/recipes.html`
- Create: `tests/test_recipes_api.py`
- Modify: `server.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_recipes_api.py`:

```python
def test_recipes_page_loads(recipes_client):
    resp = recipes_client.get("/recipes/")
    assert resp.status_code == 200
    assert b"Recipes" in resp.data


def test_health_reports_seeded_sections(recipes_client):
    resp = recipes_client.get("/recipes/api/sections")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.get_json()["sections"]]
    assert names[0] == "Produce & Fancy Cheese"
    assert names[-1] == "Unsorted"
```

- [ ] **Step 2: Add the client fixture**

Append to `tests/conftest.py`. It reuses the existing `app` fixture so the
blueprint is exercised through the real Flask app, not a synthetic one:

```python
@pytest.fixture
def recipes_client(app):
    """Test client with edit rights, against the real app + blueprint."""
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["can_edit"] = True
    return c


@pytest.fixture
def reader_client(app):
    """Test client with no edit rights — for asserting writes are gated."""
    return app.app.test_client()
```

- [ ] **Step 3: Run to verify it fails**

Run: `./.venv/bin/pytest tests/test_recipes_api.py -v`
Expected: FAIL — 404, the blueprint is not registered.

- [ ] **Step 4: Write the blueprint factory**

Create `recipes/__init__.py`:

```python
"""Family recipes + store list — a blueprint inside the SounderServer app.

Mounted at /recipes. Owns its own SQLite database and shares nothing with the
soundboard except the Flask session (for edit rights) and the display name the
frontend keeps in localStorage.
"""

from .api import bp as recipes_bp

__all__ = ["recipes_bp"]
```

- [ ] **Step 5: Write the API skeleton**

Create `recipes/api.py`. The database is opened at import from `DATA_DIR`,
matching `server.py:229` so the test fixtures' `tmp_path` isolation works:

```python
import os
import time

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
```

- [ ] **Step 6: Write the placeholder page**

Create `templates/recipes.html` — replaced wholesale in Task 11, but it must
render now so the mount is provable:

```html
<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Recipes</title>
<h1>Recipes</h1>
```

- [ ] **Step 7: Register the blueprint**

In `server.py`, immediately after `app = Flask(__name__)` (line 59):

```python
from recipes import recipes_bp
app.register_blueprint(recipes_bp)
```

The blueprint declares its own `url_prefix`, so do not pass one here.

- [ ] **Step 8: Run the tests**

Run: `./.venv/bin/pytest tests/test_recipes_api.py -v`
Expected: PASS, 2 tests.

Then confirm nothing broke on the soundboard side:

Run: `./.venv/bin/pytest tests/ -v`
Expected: PASS, all pre-existing tests still green.

- [ ] **Step 9: Commit**

```bash
git add recipes/__init__.py recipes/api.py templates/recipes.html \
        tests/test_recipes_api.py tests/conftest.py server.py
git commit -m "feat(recipes): mount the blueprint at /recipes"
```

---

## Task 5: Pantry API

The pantry item is the canonical grocery thing — it owns the store section, the
staple flag, and the Shaws product link. Filing something once must teach the
system permanently, so this comes before anything that consumes it.

**Files:**
- Modify: `recipes/api.py`
- Modify: `tests/test_recipes_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recipes_api.py`:

```python
def _mk_pantry(client, name, **kw):
    body = {"name": name, "who": "brandon"}
    body.update(kw)
    return client.post("/recipes/api/pantry", json=body).get_json()


def test_new_pantry_item_lands_in_unsorted(recipes_client):
    item = _mk_pantry(recipes_client, "Mystery powder")
    assert item["section_name"] == "Unsorted"


def test_filing_an_item_sticks(recipes_client):
    item = _mk_pantry(recipes_client, "Gruyere")
    resp = recipes_client.patch(
        f"/recipes/api/pantry/{item['id']}",
        json={"subsection": "fancy cheese", "who": "brandon"},
    )
    assert resp.status_code == 200
    again = recipes_client.get("/recipes/api/pantry").get_json()["items"]
    filed = [i for i in again if i["name"] == "Gruyere"][0]
    assert filed["section_name"] == "Produce & Fancy Cheese"
    assert filed["subsection_name"] == "fancy cheese"


def test_pantry_is_returned_in_store_order(recipes_client):
    _mk_pantry(recipes_client, "Trash bags", subsection="home")
    _mk_pantry(recipes_client, "Apples", subsection="produce")
    _mk_pantry(recipes_client, "Cumin", subsection="spices")
    names = [i["name"] for i in
             recipes_client.get("/recipes/api/pantry").get_json()["items"]]
    assert names == ["Apples", "Cumin", "Trash bags"]


def test_staple_flag_toggles(recipes_client):
    item = _mk_pantry(recipes_client, "Olive oil", subsection="baking")
    recipes_client.patch(f"/recipes/api/pantry/{item['id']}",
                         json={"is_staple": True, "who": "brandon"})
    items = recipes_client.get("/recipes/api/pantry").get_json()["items"]
    assert [i for i in items if i["name"] == "Olive oil"][0]["is_staple"] is True


def test_shaws_product_is_stored_on_the_pantry_item(recipes_client):
    item = _mk_pantry(recipes_client, "Milk", subsection="dairy")
    recipes_client.patch(f"/recipes/api/pantry/{item['id']}", json={
        "shaws_url": "https://shaws.com/p/whole-milk-gal",
        "buy_unit": "1 gal",
        "who": "brandon",
    })
    items = recipes_client.get("/recipes/api/pantry").get_json()["items"]
    milk = [i for i in items if i["name"] == "Milk"][0]
    assert milk["buy_unit"] == "1 gal"
    assert "whole-milk-gal" in milk["shaws_url"]


def test_alias_resolves_to_the_same_item(recipes_client):
    item = _mk_pantry(recipes_client, "Green onions", subsection="produce")
    recipes_client.post(f"/recipes/api/pantry/{item['id']}/alias",
                        json={"alias": "scallions", "who": "brandon"})
    resolved = recipes_client.get(
        "/recipes/api/pantry/resolve?name=Scallions").get_json()
    assert resolved["id"] == item["id"]


def test_writes_require_login(reader_client):
    resp = reader_client.post("/recipes/api/pantry",
                              json={"name": "Sneaky", "who": "nobody"})
    assert resp.status_code == 403


def test_reads_do_not_require_login(reader_client):
    assert reader_client.get("/recipes/api/pantry").status_code == 200
```

- [ ] **Step 2: Run to verify they fail**

Run: `./.venv/bin/pytest tests/test_recipes_api.py -k pantry -v`
Expected: FAIL — 404 on `/recipes/api/pantry`.

- [ ] **Step 3: Implement the pantry routes**

Append to `recipes/api.py`:

```python
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
        "SELECT id FROM subsection WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return row["id"] if row else None


def _pantry_row(item_id):
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
        "SELECT id FROM pantry_item WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        return row["id"]
    row = CONN.execute(
        "SELECT pantry_item_id AS id FROM pantry_alias WHERE alias = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    return row["id"] if row else None


def get_or_create_pantry(name, subsection_id=None):
    """Resolve a name to a pantry item, creating it in Unsorted if new.

    This is what makes filing permanent: an ingredient we have never seen gets a
    real pantry row immediately, so the one tap that files it is remembered.
    """
    existing = resolve_pantry(name)
    if existing:
        return existing
    with _db.LOCK:
        cur = CONN.execute(
            "INSERT INTO pantry_item(name, subsection_id) VALUES(?,?)",
            (name.strip(), subsection_id or _unsorted_subsection_id()),
        )
        CONN.commit()
    return cur.lastrowid


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
```

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/pytest tests/test_recipes_api.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add recipes/api.py tests/test_recipes_api.py
git commit -m "feat(recipes): pantry items, filing, staples, aliases, Shaws links"
```

---

## Task 6: Recipe CRUD and import

**Files:**
- Modify: `recipes/api.py`
- Modify: `tests/test_recipes_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recipes_api.py`:

```python
CHILI = {
    "name": "Black Bean Chili",
    "source_name": "smitten kitchen",
    "source_url": "https://smittenkitchen.com/chili",
    "servings": 6,
    "time_minutes": 45,
    "instructions": "Cook it.",
    "ingredients": [
        "2 yellow onions, diced",
        "3 cans black beans",
        "28 oz crushed tomatoes",
        "1 green pepper",
        "2 pkg Impossible grounds",
        "1 bunch cilantro",
        "1 tbsp cumin",
    ],
    "who": "brandon",
}


def _mk_recipe(client, body=None):
    return client.post("/recipes/api/recipes", json=body or CHILI).get_json()


def test_creating_a_recipe_parses_its_ingredients(recipes_client):
    r = _mk_recipe(recipes_client)
    by_raw = {i["raw_text"]: i for i in r["ingredients"]}
    onions = by_raw["2 yellow onions, diced"]
    assert onions["qty"] == 2
    assert onions["unit"] == "each"
    assert onions["prep"] == "diced"


def test_creating_a_recipe_creates_pantry_items(recipes_client):
    _mk_recipe(recipes_client)
    names = [i["name"] for i in
             recipes_client.get("/recipes/api/pantry").get_json()["items"]]
    assert "yellow onions" in names
    assert "cilantro" in names


def test_new_pantry_items_from_a_recipe_start_unsorted(recipes_client):
    _mk_recipe(recipes_client)
    items = recipes_client.get("/recipes/api/pantry").get_json()["items"]
    assert all(i["section_name"] == "Unsorted" for i in items)


def test_a_second_recipe_reuses_an_existing_pantry_item(recipes_client):
    _mk_recipe(recipes_client)
    before = len(recipes_client.get("/recipes/api/pantry").get_json()["items"])
    _mk_recipe(recipes_client, {**CHILI, "name": "Chili Again",
                                "ingredients": ["1 yellow onions"]})
    after = len(recipes_client.get("/recipes/api/pantry").get_json()["items"])
    assert after == before


def test_recipe_list_returns_summaries(recipes_client):
    _mk_recipe(recipes_client)
    rows = recipes_client.get("/recipes/api/recipes").get_json()["recipes"]
    assert rows[0]["name"] == "Black Bean Chili"
    assert rows[0]["source_name"] == "smitten kitchen"
    assert rows[0]["time_minutes"] == 45


def test_recipe_detail_keeps_the_written_lines(recipes_client):
    r = _mk_recipe(recipes_client)
    detail = recipes_client.get(f"/recipes/api/recipes/{r['id']}").get_json()
    assert "2 cloves" not in detail["ingredients"][0]["raw_text"]
    assert detail["ingredients"][0]["raw_text"] == "2 yellow onions, diced"


def test_editing_a_recipe_replaces_its_ingredients(recipes_client):
    r = _mk_recipe(recipes_client)
    recipes_client.put(f"/recipes/api/recipes/{r['id']}", json={
        **CHILI, "ingredients": ["1 yellow onions"], "who": "brandon"})
    detail = recipes_client.get(f"/recipes/api/recipes/{r['id']}").get_json()
    assert len(detail["ingredients"]) == 1


def test_archiving_hides_a_recipe_from_the_list(recipes_client):
    r = _mk_recipe(recipes_client)
    recipes_client.delete(f"/recipes/api/recipes/{r['id']}?who=brandon")
    rows = recipes_client.get("/recipes/api/recipes").get_json()["recipes"]
    assert rows == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `./.venv/bin/pytest tests/test_recipes_api.py -k recipe -v`
Expected: FAIL — 404 on `/recipes/api/recipes`.

- [ ] **Step 3: Implement the recipe routes**

Append to `recipes/api.py`:

```python
from .parse import parse_ingredient


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
```

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/pytest tests/test_recipes_api.py -v`
Expected: PASS, 18 tests.

- [ ] **Step 5: Commit**

```bash
git add recipes/api.py tests/test_recipes_api.py
git commit -m "feat(recipes): recipe CRUD with ingredient parsing and pantry linking"
```

---

## Task 7: The store list

The heart of the feature. Adding a recipe creates contributions; contributions
merge into lines; lines sort into the store walk. Get the contribution split
right and un-adding a recipe becomes trivial — get it wrong and it is impossible.

**Files:**
- Modify: `recipes/api.py`
- Modify: `tests/test_recipes_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recipes_api.py`:

```python
STIR_FRY = {
    "name": "Veggie Stir Fry",
    "ingredients": ["2 yellow onions", "2 cups rice", "1 tbsp soy sauce"],
    "who": "brandon",
}


def _add_to_list(client, recipe_id, skip=None):
    return client.post(f"/recipes/api/list/add-recipe/{recipe_id}",
                       json={"skip": skip or [], "who": "brandon"}).get_json()


def _lines(client):
    return client.get("/recipes/api/list").get_json()["lines"]


def test_adding_a_recipe_puts_its_ingredients_on_the_list(recipes_client):
    r = _mk_recipe(recipes_client)
    _add_to_list(recipes_client, r["id"])
    names = [l["name"] for l in _lines(recipes_client)]
    assert "yellow onions" in names
    assert "cilantro" in names


def test_skipped_ingredients_do_not_appear(recipes_client):
    r = _mk_recipe(recipes_client)
    pepper = [i for i in r["ingredients"] if "green pepper" in i["raw_text"]][0]
    _add_to_list(recipes_client, r["id"], skip=[pepper["id"]])
    assert "green pepper" not in [l["name"] for l in _lines(recipes_client)]


def test_two_recipes_merge_into_one_line_with_summed_quantity(recipes_client):
    chili = _mk_recipe(recipes_client)
    fry = _mk_recipe(recipes_client, STIR_FRY)
    _add_to_list(recipes_client, chili["id"])
    _add_to_list(recipes_client, fry["id"])
    onions = [l for l in _lines(recipes_client) if l["name"] == "yellow onions"]
    assert len(onions) == 1
    assert onions[0]["qty_display"] == "4"


def test_a_merged_line_names_every_recipe_that_wanted_it(recipes_client):
    chili = _mk_recipe(recipes_client)
    fry = _mk_recipe(recipes_client, STIR_FRY)
    _add_to_list(recipes_client, chili["id"])
    _add_to_list(recipes_client, fry["id"])
    onions = [l for l in _lines(recipes_client) if l["name"] == "yellow onions"][0]
    assert sorted(onions["sources"]) == ["Black Bean Chili", "Veggie Stir Fry"]


def test_list_comes_back_grouped_in_store_order(recipes_client):
    _mk_pantry(recipes_client, "Apples", subsection="produce")
    _mk_pantry(recipes_client, "Trash bags", subsection="home")
    recipes_client.post("/recipes/api/list/add",
                        json={"name": "Trash bags", "who": "kate"})
    recipes_client.post("/recipes/api/list/add",
                        json={"name": "Apples", "who": "kate"})
    sections = recipes_client.get("/recipes/api/list").get_json()["sections"]
    ordered = [s["name"] for s in sections if s["lines"]]
    assert ordered == ["Produce & Fancy Cheese", "Late Aisles"]


def test_free_text_add_lands_in_unsorted(recipes_client):
    recipes_client.post("/recipes/api/list/add",
                        json={"name": "Birthday candles", "who": "sam"})
    line = [l for l in _lines(recipes_client) if l["name"] == "Birthday candles"][0]
    assert line["section_name"] == "Unsorted"
    assert line["sources"] == ["added by sam"]


def test_checking_off_records_who(recipes_client):
    recipes_client.post("/recipes/api/list/add", json={"name": "Milk", "who": "kate"})
    line = _lines(recipes_client)[0]
    recipes_client.post(f"/recipes/api/list/line/{line['id']}/check",
                        json={"checked": True, "who": "brandon"})
    after = _lines(recipes_client)[0]
    assert after["checked"] is True
    assert after["checked_by"] == "brandon"


def test_unchecking_works(recipes_client):
    recipes_client.post("/recipes/api/list/add", json={"name": "Milk", "who": "kate"})
    line = _lines(recipes_client)[0]
    recipes_client.post(f"/recipes/api/list/line/{line['id']}/check",
                        json={"checked": True, "who": "brandon"})
    recipes_client.post(f"/recipes/api/list/line/{line['id']}/check",
                        json={"checked": False, "who": "brandon"})
    assert _lines(recipes_client)[0]["checked"] is False


def test_finish_trip_clears_checked_and_keeps_the_rest(recipes_client):
    recipes_client.post("/recipes/api/list/add", json={"name": "Milk", "who": "k"})
    recipes_client.post("/recipes/api/list/add", json={"name": "Capers", "who": "k"})
    milk = [l for l in _lines(recipes_client) if l["name"] == "Milk"][0]
    recipes_client.post(f"/recipes/api/list/line/{milk['id']}/check",
                        json={"checked": True, "who": "brandon"})
    recipes_client.post("/recipes/api/list/finish-trip", json={"who": "brandon"})
    names = [l["name"] for l in _lines(recipes_client)]
    assert names == ["Capers"]      # couldn't find the capers; they survive


def test_staples_are_reported_so_the_add_sheet_can_fold_them(recipes_client):
    r = _mk_recipe(recipes_client)
    cumin = [i for i in r["ingredients"] if "cumin" in i["raw_text"]][0]
    recipes_client.patch(f"/recipes/api/pantry/{cumin['pantry_item_id']}",
                         json={"is_staple": True, "who": "brandon"})
    detail = recipes_client.get(f"/recipes/api/recipes/{r['id']}").get_json()
    staples = [i for i in detail["ingredients"] if i["is_staple"]]
    assert [s["pantry_name"] for s in staples] == ["cumin"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `./.venv/bin/pytest tests/test_recipes_api.py -k list -v`
Expected: FAIL — 404 on `/recipes/api/list`.

- [ ] **Step 3: Implement the list routes**

Append to `recipes/api.py`:

```python
from . import units


def _find_or_make_line(pantry_item_id=None, free_text=None):
    """One line per grocery thing. Unchecked lines merge; checked ones do not.

    A checked line is already in the cart, so a new claim on the same item needs
    its own line rather than silently reviving something you already bought.
    """
    if pantry_item_id:
        row = CONN.execute(
            "SELECT id FROM list_line WHERE pantry_item_id=? AND checked=0",
            (pantry_item_id,),
        ).fetchone()
    else:
        row = CONN.execute(
            "SELECT id FROM list_line WHERE free_text=? COLLATE NOCASE AND checked=0",
            (free_text,),
        ).fetchone()
    if row:
        return row["id"]
    with _db.LOCK:
        cur = CONN.execute(
            "INSERT INTO list_line(pantry_item_id, free_text, created_at)"
            " VALUES(?,?,?)",
            (pantry_item_id, free_text, int(time.time())),
        )
        CONN.commit()
    return cur.lastrowid


@bp.route("/api/list/add-recipe/<int:recipe_id>", methods=["POST"])
def api_list_add_recipe(recipe_id):
    gate = need_edit()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    skip = set(body.get("skip") or [])
    person = who()
    rows = CONN.execute(
        "SELECT * FROM recipe_ingredient WHERE recipe_id=? ORDER BY position",
        (recipe_id,),
    ).fetchall()
    for ing in rows:
        if ing["id"] in skip:
            continue
        line_id = _find_or_make_line(pantry_item_id=ing["pantry_item_id"],
                                     free_text=None if ing["pantry_item_id"] else ing["raw_text"])
        with _db.LOCK:
            CONN.execute("""
                INSERT INTO list_contribution
                    (list_line_id, recipe_id, added_by, qty, unit, raw_text)
                VALUES (?,?,?,?,?,?)
            """, (line_id, recipe_id, person, ing["qty"], ing["unit"], ing["raw_text"]))
            CONN.commit()
    with _db.LOCK:
        CONN.execute(
            "INSERT OR IGNORE INTO meal_plan(recipe_id, added_by, added_at)"
            " VALUES(?,?,?)",
            (recipe_id, person, int(time.time())),
        )
        CONN.commit()
    _db.bump_version(CONN)
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
        CONN.execute("""
            INSERT INTO list_contribution
                (list_line_id, recipe_id, added_by, qty, unit, raw_text)
            VALUES (?, NULL, ?, ?, ?, ?)
        """, (line_id, person, body.get("qty"), body.get("unit"), name))
        CONN.commit()
    _db.bump_version(CONN)
    return jsonify(_list_json())


@bp.route("/api/list/line/<int:line_id>/check", methods=["POST"])
def api_list_check(line_id):
    gate = need_edit()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    checked = bool(body.get("checked"))
    with _db.LOCK:
        CONN.execute(
            "UPDATE list_line SET checked=?, checked_by=?, checked_at=? WHERE id=?",
            (int(checked), who() if checked else None,
             int(time.time()) if checked else None, line_id),
        )
        CONN.commit()
    _db.bump_version(CONN)
    return jsonify(_list_json())


@bp.route("/api/list/finish-trip", methods=["POST"])
def api_list_finish_trip():
    gate = need_edit()
    if gate:
        return gate
    with _db.LOCK:
        CONN.execute("DELETE FROM list_line WHERE checked=1")
        CONN.commit()
    _db.bump_version(CONN)
    return jsonify(_list_json())


@bp.route("/api/list")
def api_list():
    return jsonify(_list_json())


def _list_json():
    """The store list, merged and grouped into the walking order."""
    rows = CONN.execute("""
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
        contribs = CONN.execute("""
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

    section_rows = CONN.execute(
        "SELECT name FROM section ORDER BY position"
    ).fetchall()
    sections = [{"name": s["name"], "lines": by_section.get(s["name"], [])}
                for s in section_rows]

    meals = CONN.execute("""
        SELECT r.id, r.name FROM meal_plan m
        JOIN recipe r ON r.id = m.recipe_id
        ORDER BY m.added_at
    """).fetchall()

    return {
        "version": _db.get_version(CONN),
        "lines": lines,
        "sections": sections,
        "meals": [dict(m) for m in meals],
    }
```

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/pytest tests/test_recipes_api.py -v`
Expected: PASS, 28 tests.

- [ ] **Step 5: Commit**

```bash
git add recipes/api.py tests/test_recipes_api.py
git commit -m "feat(recipes): store list with merging, store ordering, and trips"
```

---

## Task 8: Removing a recipe, and the sync poll

Pulling a recipe off the list must withdraw only its own claims. This is the
payoff for the contribution split, and it needs its own test because getting it
wrong silently corrupts other recipes' quantities.

**Files:**
- Modify: `recipes/api.py`
- Modify: `tests/test_recipes_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recipes_api.py`:

```python
def test_removing_a_recipe_leaves_the_other_recipes_share(recipes_client):
    chili = _mk_recipe(recipes_client)
    fry = _mk_recipe(recipes_client, STIR_FRY)
    _add_to_list(recipes_client, chili["id"])
    _add_to_list(recipes_client, fry["id"])
    recipes_client.post(f"/recipes/api/list/remove-recipe/{chili['id']}",
                        json={"who": "brandon"})
    onions = [l for l in _lines(recipes_client) if l["name"] == "yellow onions"]
    assert len(onions) == 1
    assert onions[0]["qty_display"] == "2"       # stir fry's 2, not 4, not gone


def test_removing_a_recipe_deletes_lines_nothing_else_wanted(recipes_client):
    chili = _mk_recipe(recipes_client)
    _add_to_list(recipes_client, chili["id"])
    recipes_client.post(f"/recipes/api/list/remove-recipe/{chili['id']}",
                        json={"who": "brandon"})
    assert _lines(recipes_client) == []


def test_removing_a_recipe_drops_it_from_the_meal_strip(recipes_client):
    chili = _mk_recipe(recipes_client)
    _add_to_list(recipes_client, chili["id"])
    recipes_client.post(f"/recipes/api/list/remove-recipe/{chili['id']}",
                        json={"who": "brandon"})
    meals = recipes_client.get("/recipes/api/list").get_json()["meals"]
    assert meals == []


def test_finish_trip_keeps_the_meal_strip(recipes_client):
    chili = _mk_recipe(recipes_client)
    _add_to_list(recipes_client, chili["id"])
    for line in _lines(recipes_client):
        recipes_client.post(f"/recipes/api/list/line/{line['id']}/check",
                            json={"checked": True, "who": "brandon"})
    recipes_client.post("/recipes/api/list/finish-trip", json={"who": "brandon"})
    data = recipes_client.get("/recipes/api/list").get_json()
    assert data["lines"] == []
    assert [m["name"] for m in data["meals"]] == ["Black Bean Chili"]


def test_poll_returns_nothing_when_unchanged(recipes_client):
    version = recipes_client.get("/recipes/api/list").get_json()["version"]
    resp = recipes_client.get(f"/recipes/api/list/poll?since={version}").get_json()
    assert resp["changed"] is False
    assert "sections" not in resp


def test_poll_returns_the_list_when_changed(recipes_client):
    version = recipes_client.get("/recipes/api/list").get_json()["version"]
    recipes_client.post("/recipes/api/list/add", json={"name": "Milk", "who": "k"})
    resp = recipes_client.get(f"/recipes/api/list/poll?since={version}").get_json()
    assert resp["changed"] is True
    assert [l["name"] for l in resp["lines"]] == ["Milk"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `./.venv/bin/pytest tests/test_recipes_api.py -k "remove_recipe or poll" -v`
Expected: FAIL — 404.

- [ ] **Step 3: Implement**

Append to `recipes/api.py`:

```python
@bp.route("/api/list/remove-recipe/<int:recipe_id>", methods=["POST"])
def api_list_remove_recipe(recipe_id):
    """Withdraw one recipe's claims, then delete any line nobody wants anymore.

    Every line always carries at least one contribution — manual adds create one
    with recipe_id NULL — so "no contributions left" is an exact test for
    orphaned.
    """
    gate = need_edit()
    if gate:
        return gate
    with _db.LOCK:
        CONN.execute("DELETE FROM list_contribution WHERE recipe_id=?", (recipe_id,))
        CONN.execute("""
            DELETE FROM list_line WHERE id NOT IN
                (SELECT DISTINCT list_line_id FROM list_contribution)
        """)
        CONN.execute("DELETE FROM meal_plan WHERE recipe_id=?", (recipe_id,))
        CONN.commit()
    _db.bump_version(CONN)
    return jsonify(_list_json())


@bp.route("/api/list/poll")
def api_list_poll():
    """Cheap change check. Returns the full list only when the version moved."""
    try:
        since = int(request.args.get("since", -1))
    except ValueError:
        since = -1
    current = _db.get_version(CONN)
    if since == current:
        return jsonify({"changed": False, "version": current})
    payload = _list_json()
    payload["changed"] = True
    return jsonify(payload)
```

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/pytest tests/test_recipes_api.py -v`
Expected: PASS, 34 tests.

- [ ] **Step 5: Commit**

```bash
git add recipes/api.py tests/test_recipes_api.py
git commit -m "feat(recipes): remove a recipe from the list; version-based sync poll"
```

---

## Task 9: Shaws export

What an out-of-band Claude session reads to fill a pickup cart. The server never
drives a browser — it just publishes the unchecked list with whatever product
links the pantry has accumulated.

**Files:**
- Modify: `recipes/api.py`
- Modify: `tests/test_recipes_api.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_export_lists_unchecked_items_with_their_shaws_links(recipes_client):
    item = _mk_pantry(recipes_client, "Milk", subsection="dairy")
    recipes_client.patch(f"/recipes/api/pantry/{item['id']}", json={
        "shaws_url": "https://shaws.com/p/whole-milk-gal",
        "buy_unit": "1 gal", "who": "brandon"})
    recipes_client.post("/recipes/api/list/add", json={"name": "Milk", "who": "k"})

    export = recipes_client.get("/recipes/api/list/export").get_json()
    assert export["items"][0]["name"] == "Milk"
    assert export["items"][0]["shaws_url"].endswith("whole-milk-gal")
    assert export["items"][0]["buy_unit"] == "1 gal"


def test_export_flags_items_with_no_product_link(recipes_client):
    recipes_client.post("/recipes/api/list/add",
                        json={"name": "Birthday candles", "who": "sam"})
    export = recipes_client.get("/recipes/api/list/export").get_json()
    assert export["items"][0]["shaws_url"] is None
    assert export["needs_product_link"] == ["Birthday candles"]


def test_export_omits_checked_items(recipes_client):
    recipes_client.post("/recipes/api/list/add", json={"name": "Milk", "who": "k"})
    line = _lines(recipes_client)[0]
    recipes_client.post(f"/recipes/api/list/line/{line['id']}/check",
                        json={"checked": True, "who": "brandon"})
    assert recipes_client.get("/recipes/api/list/export").get_json()["items"] == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `./.venv/bin/pytest tests/test_recipes_api.py -k export -v`
Expected: FAIL — 404.

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run the full backend suite**

Run: `./.venv/bin/pytest tests/ -v`
Expected: PASS — all recipe tests plus every pre-existing soundboard test.

- [ ] **Step 5: Commit**

```bash
git add recipes/api.py tests/test_recipes_api.py
git commit -m "feat(recipes): Shaws pickup export endpoint"
```

---

# Frontend

Vanilla JS, no build step, matching `templates/index.html`. The backend is done
and tested at this point, so every remaining task is verified by hand in a
browser at 390 px width (Firefox/Chrome responsive mode, iPhone 14 preset).

**Phone rules, applied to every screen below:**
- Body text 17 px. Nothing below 14 px except the muted attribution line.
- Every tappable row ≥ 44 px tall; whole rows are tappable, not just the checkbox.
- `viewport-fit=cover` plus `env(safe-area-inset-bottom)` padding on the tab bar,
  or the home indicator eats the last row.
- No hover-dependent affordances.

## Task 10: Frontend shell

**Files:**
- Modify: `templates/recipes.html`

- [ ] **Step 1: Replace the placeholder page**

Write the full shell — document head, theme, tab bar, name handling, and the
polling loop. Everything after this task fills in `renderRecipes()`,
`renderList()`, and `renderPantry()`.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#111418">
<title>Recipes</title>
<style>
  :root{
    --bg:#111418; --panel:#171c23; --panel2:#1e242c; --line:rgba(255,255,255,.08);
    --txt:#e8eaed; --dim:rgba(232,234,237,.45); --accent:#6aa9ff; --go:#3d8b5f;
  }
  *{box-sizing:border-box}
  html,body{margin:0;background:var(--bg);color:var(--txt);
    font:400 17px/1.35 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;
    -webkit-font-smoothing:antialiased; overscroll-behavior-y:contain}
  body{padding-bottom:calc(64px + env(safe-area-inset-bottom))}
  header{position:sticky;top:0;z-index:5;background:var(--panel);
    border-bottom:1px solid var(--line);padding:8px 16px 12px}
  header .top{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
  header h1{font-size:26px;font-weight:700;letter-spacing:-.4px;margin:0}
  header .meta{font-size:15px;color:var(--dim)}
  input.name{background:var(--panel2);border:1px solid var(--line);color:var(--txt);
    border-radius:8px;padding:6px 9px;font-size:15px;width:110px}
  .search{width:100%;margin-top:10px;padding:11px 13px;border-radius:11px;
    background:var(--panel2);border:1px solid var(--line);color:var(--txt);font-size:16px}
  .row{display:flex;align-items:center;gap:14px;padding:11px 16px;min-height:56px;
    border-bottom:1px solid rgba(255,255,255,.05)}
  .row .nm{flex:1;min-width:0}
  .row .nm b{font-weight:500;display:block}
  .row .nm small{display:block;color:var(--dim);font-size:14px;margin-top:2px}
  .sec{padding:14px 16px 6px;font-size:12px;letter-spacing:.11em;text-transform:uppercase;
    color:var(--dim);background:#151920;border-top:1px solid var(--line);
    position:sticky;top:0;z-index:1}
  .bx{width:26px;height:26px;border-radius:7px;border:2px solid rgba(255,255,255,.3);
    flex:none;text-align:center;line-height:23px;font-size:16px}
  .bx.on{background:var(--go);border-color:var(--go);color:#fff}
  .fold{padding:13px 16px;font-size:15px;color:var(--dim);background:#13171d;
    border-bottom:1px solid var(--line)}
  .btn{display:block;width:calc(100% - 32px);margin:16px;padding:16px;border:0;
    border-radius:14px;background:var(--go);color:#fff;font-size:17px;font-weight:600}
  .btn.quiet{background:var(--panel2);color:var(--txt)}
  nav{position:fixed;bottom:0;left:0;right:0;display:flex;background:var(--panel);
    border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom);z-index:10}
  nav button{flex:1;background:0;border:0;color:var(--dim);font-size:12px;
    padding:9px 0 7px;font-family:inherit}
  nav button em{display:block;font-style:normal;font-size:22px;margin-bottom:2px}
  nav button.sel{color:var(--accent)}
  .empty{padding:48px 24px;text-align:center;color:var(--dim)}
  .strip{display:flex;gap:8px;overflow-x:auto;padding:10px 16px;
    border-bottom:1px solid var(--line);-webkit-overflow-scrolling:touch}
  .strip span{flex:none;padding:6px 13px;border-radius:16px;background:var(--panel2);
    font-size:14px;white-space:nowrap}
</style>
</head>
<body>

<header>
  <div class="top">
    <h1 id="title">Recipes</h1>
    <div style="display:flex;gap:8px;align-items:center">
      <span class="meta" id="count"></span>
      <input class="name" id="name" placeholder="your name">
    </div>
  </div>
  <div id="hdextra"></div>
</header>

<main id="main"></main>

<nav>
  <button data-tab="recipes" class="sel"><em>📖</em>Recipes</button>
  <button data-tab="list"><em>🛒</em>List</button>
  <button data-tab="pantry"><em>🧂</em>Pantry</button>
</nav>

<script>
const $  = s => document.querySelector(s);
const el = (t, c, h) => { const n=document.createElement(t);
  if(c) n.className=c; if(h!=null) n.innerHTML=h; return n; };
const esc = s => String(s==null?'':s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// Display name is shared with the soundboard — same localStorage key, so if you
// have ever used the soundboard your name is already filled in here.
$('#name').value = localStorage.getItem('ss_name') || '';
$('#name').onchange = () => localStorage.setItem('ss_name', $('#name').value.trim());
const who = () => $('#name').value.trim() || 'someone';

const api = async (path, opts={}) => {
  const o = {headers:{'Content-Type':'application/json'}, ...opts};
  if (o.body && typeof o.body === 'object') o.body = JSON.stringify({...o.body, who: who()});
  const r = await fetch('/recipes/api' + path, o);
  if (!r.ok) { console.warn('api', path, r.status); return null; }
  return r.json();
};

let TAB = 'recipes';
let STATE = {recipes: [], list: null, pantry: [], version: -1, queued: new Set()};

document.querySelectorAll('nav button').forEach(b => b.onclick = () => {
  TAB = b.dataset.tab;
  document.querySelectorAll('nav button').forEach(x => x.classList.toggle('sel', x===b));
  render();
});

function render(){
  $('#hdextra').innerHTML = '';
  if (TAB === 'recipes') renderRecipes();
  else if (TAB === 'list') renderList();
  else renderPantry();
}

async function refresh(){
  if (TAB === 'recipes' && !STATE.recipes.length)
    STATE.recipes = (await api('/recipes'))?.recipes || [];
  if (TAB === 'pantry')
    STATE.pantry = (await api('/pantry'))?.items || [];
  if (!STATE.list) STATE.list = await api('/list');
  if (STATE.list) STATE.version = STATE.list.version;
  render();
}

// Cross-phone sync: cheap version check, full payload only when something moved.
async function poll(){
  if (TAB !== 'list') return;
  const r = await api('/list/poll?since=' + STATE.version);
  if (r && r.changed) { STATE.list = r; STATE.version = r.version; render(); }
}
setInterval(poll, 4000);
document.addEventListener('visibilitychange', () => { if(!document.hidden) poll(); });

refresh();
</script>
</body>
</html>
```

- [ ] **Step 2: Verify the shell loads**

Run: `DATA_DIR=$(mktemp -d) SOUND_DIR=$(mktemp -d) ./.venv/bin/python server.py`
Open `http://127.0.0.1:5050/recipes/` at 390 px width.
Expected: header, empty body, three tabs at the bottom. Tabs switch without error.
Console must be clean.

- [ ] **Step 3: Commit**

```bash
git add templates/recipes.html
git commit -m "feat(recipes): frontend shell, tab nav, and sync polling"
```

---

## Task 11: The store list screen

The screen that gets used most and the one with the agreed behavior: checked
items **sink into a per-section fold**, so the screen only ever shows what is
still needed.

**Files:**
- Modify: `templates/recipes.html`

- [ ] **Step 1: Implement `renderList()`**

Add inside the `<script>` block, before `refresh()`:

```js
function renderList(){
  const d = STATE.list;
  $('#title').textContent = 'Store List';
  if (!d) { $('#main').innerHTML = '<div class="empty">Loading…</div>'; return; }

  const left = d.lines.filter(l => !l.checked).length;
  $('#count').textContent = left + ' left · ' + d.lines.length;

  // meal-plan strip: what these groceries are for
  if (d.meals.length){
    const strip = el('div','strip');
    d.meals.forEach(m => {
      const chip = el('span', null, esc(m.name) + ' ✕');
      chip.onclick = async () => {
        if (!confirm('Take ' + m.name + ' off the list?')) return;
        STATE.list = await api('/list/remove-recipe/' + m.id, {method:'POST', body:{}});
        render();
      };
      strip.appendChild(chip);
    });
    $('#hdextra').appendChild(strip);
  }

  const main = el('div');

  // quick add — "we're out of trash bags", straight onto the list
  const add = el('input','search');
  add.placeholder = 'Add anything…';
  add.onkeydown = async e => {
    if (e.key !== 'Enter' || !add.value.trim()) return;
    STATE.list = await api('/list/add', {method:'POST', body:{name: add.value.trim()}});
    add.value = ''; render();
  };
  $('#hdextra').appendChild(add);

  let any = false;
  for (const section of d.sections){
    if (!section.lines.length) continue;          // never show an empty aisle
    any = true;
    main.appendChild(el('div','sec', esc(section.name)));

    const open = section.lines.filter(l => !l.checked);
    const done = section.lines.filter(l => l.checked);

    open.forEach(l => main.appendChild(lineRow(l)));

    if (done.length){
      const fold = el('div','fold', '✓ ' + done.length + ' in the cart');
      let shown = false;
      const holder = el('div');
      fold.onclick = () => {
        shown = !shown;
        holder.innerHTML = '';
        if (shown) done.forEach(l => holder.appendChild(lineRow(l)));
      };
      main.appendChild(fold);
      main.appendChild(holder);
    }
  }

  if (!any) main.appendChild(el('div','empty',
    'Nothing on the list.<br>Add a recipe, or type something above.'));

  if (d.lines.some(l => l.checked)){
    const finish = el('button','btn', 'Finish trip');
    finish.onclick = async () => {
      STATE.list = await api('/list/finish-trip', {method:'POST', body:{}});
      render();
    };
    main.appendChild(finish);
  }

  $('#main').innerHTML = '';
  $('#main').appendChild(main);
}

function lineRow(l){
  const row = el('div','row');
  row.appendChild(el('div', 'bx' + (l.checked ? ' on' : ''), l.checked ? '✓' : ''));

  const sub = [l.sources.join(' · '), l.buy_unit ? '→ ' + l.buy_unit : '']
                .filter(Boolean).join(' · ');
  const nm = el('div','nm',
    '<b>' + esc(l.name) + '</b>' + (sub ? '<small>' + esc(sub) + '</small>' : ''));
  row.appendChild(nm);

  if (l.qty_display) row.appendChild(el('div', null,
    '<span style="color:var(--dim);font-size:15px">' + esc(l.qty_display) + '</span>'));

  if (l.checked) { nm.style.opacity = .4; nm.style.textDecoration = 'line-through'; }

  // whole row is the target, not just the little box
  row.onclick = async () => {
    STATE.list = await api('/list/line/' + l.id + '/check',
                           {method:'POST', body:{checked: !l.checked}});
    render();
  };
  return row;
}
```

- [ ] **Step 2: Verify by hand**

With the server running, in a second terminal seed some data:

```bash
curl -s -X POST 127.0.0.1:5050/recipes/api/list/add \
  -H 'Content-Type: application/json' -d '{"name":"Trash bags","who":"test"}'
```

Open `/recipes/`, go to the List tab. Check:
- The item appears under **Unsorted**.
- Tapping the row greys it and it disappears into a `✓ 1 in the cart` fold.
- Tapping the fold reveals it; tapping it again un-checks it.
- **Finish trip** appears only when something is checked, and clears only those.
- Open the page in two browser windows; check an item in one and it greys in the
  other within ~4 seconds.

- [ ] **Step 3: Commit**

```bash
git add templates/recipes.html
git commit -m "feat(recipes): store list screen with per-section done-folds"
```

---

## Task 12: Recipes screen and the add sheet

Dense rows — seven per screen at 390 px, no photo required, source line visible.
Tap ⊕ to queue, tap the name to open.

**Files:**
- Modify: `templates/recipes.html`

- [ ] **Step 1: Implement `renderRecipes()` and the add sheet**

```js
let RECIPE_FILTER = '';

function renderRecipes(){
  $('#title').textContent = 'Recipes';
  $('#count').textContent = STATE.queued.size ? STATE.queued.size + ' selected' : '';

  const search = el('input','search');
  search.placeholder = 'Search recipes…';
  search.value = RECIPE_FILTER;
  search.oninput = () => { RECIPE_FILTER = search.value.toLowerCase(); render(); };
  $('#hdextra').appendChild(search);

  const main = el('div');
  const shown = STATE.recipes.filter(r =>
    !RECIPE_FILTER || r.name.toLowerCase().includes(RECIPE_FILTER));

  if (!shown.length){
    main.appendChild(el('div','empty',
      STATE.recipes.length ? 'No matches.' : 'No recipes yet.'));
  }

  shown.forEach(r => {
    const row = el('div','row');
    row.appendChild(el('div', null,
      '<div style="width:46px;height:46px;border-radius:10px;font-size:24px;' +
      'display:flex;align-items:center;justify-content:center;' +
      'background:linear-gradient(135deg,#2a3742,#1d2830)">' +
      (r.photo_url ? '' : '🍽️') + '</div>'));

    const bits = [r.time_minutes ? r.time_minutes + ' min' : '',
                  r.servings ? 'serves ' + r.servings : '',
                  r.source_name || ''].filter(Boolean).join(' · ');
    const nm = el('div','nm',
      '<b>' + esc(r.name) + '</b><small>' + esc(bits) + '</small>');
    nm.onclick = () => openRecipe(r.id);
    row.appendChild(nm);

    const queued = STATE.queued.has(r.id);
    const plus = el('div', null,
      '<div style="width:34px;height:34px;border-radius:50%;font-size:' +
      (queued?'17':'21') + 'px;line-height:31px;text-align:center;border:2px solid ' +
      (queued ? 'var(--go);background:var(--go);color:#fff' :
                'rgba(255,255,255,.28);color:var(--dim)') + '">' +
      (queued ? '✓' : '+') + '</div>');
    plus.onclick = e => {
      e.stopPropagation();
      queued ? STATE.queued.delete(r.id) : STATE.queued.add(r.id);
      render();
    };
    row.appendChild(plus);
    main.appendChild(row);
  });

  if (STATE.queued.size){
    const go = el('button','btn', 'Add ' + STATE.queued.size + ' to list');
    go.onclick = () => runAddSheets([...STATE.queued]);
    main.appendChild(go);
  }

  const add = el('button','btn quiet','+ New recipe');
  add.onclick = () => openRecipeEditor(null);
  main.appendChild(add);

  $('#main').innerHTML = '';
  $('#main').appendChild(main);
}

// One sheet per queued recipe, in sequence — you confirm each, then it moves on.
async function runAddSheets(ids){
  for (const id of ids){
    const r = await api('/recipes/' + id);
    if (r) await addSheet(r);
    STATE.queued.delete(id);
  }
  STATE.list = await api('/list');
  TAB = 'list';
  document.querySelectorAll('nav button').forEach(
    x => x.classList.toggle('sel', x.dataset.tab === 'list'));
  render();
}

function addSheet(recipe){
  return new Promise(resolve => {
    $('#title').textContent = recipe.name;
    $('#hdextra').innerHTML = '';
    const main = el('div');
    main.appendChild(el('div', null,
      '<div style="padding:16px 16px 8px"><b style="font-size:20px">Add to store' +
      ' list</b><small style="display:block;color:var(--dim);font-size:14px;' +
      'margin-top:3px">Uncheck anything you already have</small></div>'));

    // staples arrive pre-unchecked and folded — you stop re-deciding about salt
    const skip = new Set(recipe.ingredients.filter(i => i.is_staple).map(i => i.id));
    const normal = recipe.ingredients.filter(i => !i.is_staple);
    const staples = recipe.ingredients.filter(i => i.is_staple);

    const cta = el('button','btn','');
    const paint = () => cta.textContent =
      'Add ' + (recipe.ingredients.length - skip.size) + ' items to list';

    const ingRow = i => {
      const row = el('div','row');
      const box = el('div','bx' + (skip.has(i.id) ? '' : ' on'), skip.has(i.id) ? '' : '✓');
      const nm = el('div','nm','<b style="font-weight:400">' + esc(i.raw_text) + '</b>');
      row.appendChild(box); row.appendChild(nm);
      row.onclick = () => {
        skip.has(i.id) ? skip.delete(i.id) : skip.add(i.id);
        box.className = 'bx' + (skip.has(i.id) ? '' : ' on');
        box.textContent = skip.has(i.id) ? '' : '✓';
        nm.style.opacity = skip.has(i.id) ? .35 : 1;
        nm.style.textDecoration = skip.has(i.id) ? 'line-through' : 'none';
        paint();
      };
      if (skip.has(i.id)) { nm.style.opacity = .35; nm.style.textDecoration='line-through'; }
      return row;
    };

    normal.forEach(i => main.appendChild(ingRow(i)));

    if (staples.length){
      const names = staples.map(s => s.pantry_name || s.raw_text).join(', ');
      const fold = el('div','fold',
        '▸ ' + staples.length + ' staples skipped — ' + esc(names));
      const holder = el('div');
      let open = false;
      fold.onclick = () => {
        open = !open; holder.innerHTML = '';
        if (open) staples.forEach(i => holder.appendChild(ingRow(i)));
      };
      main.appendChild(fold); main.appendChild(holder);
    }

    paint();
    cta.onclick = async () => {
      await api('/list/add-recipe/' + recipe.id,
                {method:'POST', body:{skip:[...skip]}});
      resolve();
    };
    main.appendChild(cta);

    const cancel = el('button','btn quiet','Skip this one');
    cancel.onclick = () => resolve();
    main.appendChild(cancel);

    $('#main').innerHTML = '';
    $('#main').appendChild(main);
  });
}
```

- [ ] **Step 2: Verify by hand**

Seed a recipe:

```bash
curl -s -X POST 127.0.0.1:5050/recipes/api/recipes -H 'Content-Type: application/json' \
 -d '{"name":"Black Bean Chili","source_name":"smitten kitchen","servings":6,
      "time_minutes":45,"who":"test",
      "ingredients":["2 yellow onions, diced","3 cans black beans",
                     "1 bunch cilantro","1 tbsp cumin"]}'
```

Check at 390 px:
- The row shows `45 min · serves 6 · smitten kitchen`.
- ⊕ turns green and the badge counts; the button reads `Add 1 to list`.
- The add sheet lists all four lines pre-checked; unchecking updates the count.
- Confirming lands you on the List tab with the items grouped under Unsorted.
- Flag cumin as a staple via the Pantry tab (Task 13), re-add, and confirm it
  now arrives folded and unchecked.

- [ ] **Step 3: Commit**

```bash
git add templates/recipes.html
git commit -m "feat(recipes): recipes screen, queueing, and the add-to-list sheet"
```

---

## Task 13: Pantry screen, recipe detail, and the editor

Rarely opened but load-bearing: this is where filing, staples, and Shaws links
happen, and where a recipe gets typed in or corrected.

**Files:**
- Modify: `templates/recipes.html`

- [ ] **Step 1: Implement the remaining screens**

```js
function renderPantry(){
  $('#title').textContent = 'Pantry';
  $('#count').textContent = STATE.pantry.length + ' items';

  const main = el('div');
  let lastSection = null;
  STATE.pantry.forEach(p => {
    if (p.section_name !== lastSection){
      lastSection = p.section_name;
      main.appendChild(el('div','sec', esc(lastSection)));
    }
    const row = el('div','row');
    const bits = [p.subsection_name || 'unfiled',
                  p.is_staple ? 'staple' : '',
                  p.shaws_url ? 'Shaws ✓' : ''].filter(Boolean).join(' · ');
    row.appendChild(el('div','nm',
      '<b>' + esc(p.name) + '</b><small>' + esc(bits) + '</small>'));
    row.onclick = () => editPantry(p);
    main.appendChild(row);
  });

  $('#main').innerHTML = '';
  $('#main').appendChild(main);
}

async function editPantry(p){
  const sub = prompt(
    'Sub-category for "' + p.name + '"\n\n' +
    'produce, fancy cheese, coffee & tea, cereal, breakfast, spices, baking,\n' +
    'plant meat, broths, soups, pasta, pasta sauce, canned veg, rice, asian,\n' +
    'mexican, chips, cookies, salty snacks, candy, paper, home, medicine,\n' +
    'toiletries, soda, drinks, seltzer, wine, beer, liquor, freezer, dairy, bread',
    p.subsection_name || '');
  if (sub === null) return;
  const staple = confirm('Is "' + p.name + '" a staple (always in the house)?');
  const shaws  = prompt('Shaws product URL (optional)', p.shaws_url || '') || null;
  const buy    = prompt('What do we buy? e.g. "1 gal" (optional)', p.buy_unit || '') || null;
  await api('/pantry/' + p.id, {method:'PATCH',
    body:{subsection: sub || null, is_staple: staple, shaws_url: shaws, buy_unit: buy}});
  STATE.pantry = (await api('/pantry'))?.items || [];
  render();
}

async function openRecipe(id){
  const r = await api('/recipes/' + id);
  if (!r) return;
  $('#title').textContent = r.name;
  $('#hdextra').innerHTML = '';
  const main = el('div');

  const bits = [r.time_minutes ? r.time_minutes + ' min' : '',
                r.servings ? 'serves ' + r.servings : '',
                r.source_name || ''].filter(Boolean).join(' · ');
  if (bits) main.appendChild(el('div', null,
    '<div style="padding:12px 16px;color:var(--dim);font-size:15px">' +
    esc(bits) + '</div>'));
  if (r.source_url) main.appendChild(el('div', null,
    '<div style="padding:0 16px 12px"><a href="' + esc(r.source_url) +
    '" style="color:var(--accent)">open the original</a></div>'));

  main.appendChild(el('div','sec','Ingredients'));
  r.ingredients.forEach(i => main.appendChild(
    el('div','row','<div class="nm"><b style="font-weight:400">' +
       esc(i.raw_text) + '</b></div>')));

  if (r.instructions){
    main.appendChild(el('div','sec','Method'));
    main.appendChild(el('div', null,
      '<div style="padding:14px 16px;white-space:pre-wrap">' +
      esc(r.instructions) + '</div>'));
  }

  const go = el('button','btn','Add to store list');
  go.onclick = () => runAddSheets([r.id]);
  main.appendChild(go);

  const ed = el('button','btn quiet','Edit');
  ed.onclick = () => openRecipeEditor(r);
  main.appendChild(ed);

  const back = el('button','btn quiet','Back to recipes');
  back.onclick = () => render();
  main.appendChild(back);

  $('#main').innerHTML = '';
  $('#main').appendChild(main);
}

function openRecipeEditor(r){
  $('#title').textContent = r ? 'Edit recipe' : 'New recipe';
  $('#hdextra').innerHTML = '';
  const main = el('div');
  const field = (label, value, tag='input') => {
    main.appendChild(el('div', null,
      '<div style="padding:12px 16px 4px;color:var(--dim);font-size:14px">' +
      label + '</div>'));
    const node = document.createElement(tag);
    node.className = 'search';
    node.style.margin = '0 16px';
    node.style.width = 'calc(100% - 32px)';
    if (tag === 'textarea') node.rows = 8;
    node.value = value || '';
    main.appendChild(node);
    return node;
  };

  const name    = field('Name', r?.name);
  const source  = field('Source (smitten kitchen, Grandma…)', r?.source_name);
  const url     = field('Source URL', r?.source_url);
  const serves  = field('Serves', r?.servings);
  const minutes = field('Minutes', r?.time_minutes);
  const ings    = field('Ingredients — one per line',
                        r ? r.ingredients.map(i => i.raw_text).join('\n') : '',
                        'textarea');
  const method  = field('Method', r?.instructions, 'textarea');

  const save = el('button','btn', r ? 'Save' : 'Create');
  save.onclick = async () => {
    if (!name.value.trim()) { alert('Give it a name'); return; }
    const body = {
      name: name.value.trim(), source_name: source.value.trim() || null,
      source_url: url.value.trim() || null,
      servings: parseInt(serves.value) || null,
      time_minutes: parseInt(minutes.value) || null,
      instructions: method.value, 
      ingredients: ings.value.split('\n').map(s => s.trim()).filter(Boolean),
    };
    await api(r ? '/recipes/' + r.id : '/recipes',
              {method: r ? 'PUT' : 'POST', body});
    STATE.recipes = (await api('/recipes'))?.recipes || [];
    TAB = 'recipes'; render();
  };
  main.appendChild(save);

  const cancel = el('button','btn quiet','Cancel');
  cancel.onclick = () => { TAB = 'recipes'; render(); };
  main.appendChild(cancel);

  $('#main').innerHTML = '';
  $('#main').appendChild(main);
}
```

- [ ] **Step 2: Verify the full loop by hand**

At 390 px, with no curl seeding — do it all through the UI:
1. Recipes → **+ New recipe** → type a recipe with 6 ingredients → Create.
2. Pantry → confirm all 6 ingredients appear under **Unsorted**.
3. File three of them into real sub-categories; mark one a staple.
4. Recipes → ⊕ the recipe → **Add 1 to list**.
5. List → the three filed items sit under their real sections in walking order;
   the unfiled ones are under Unsorted; the staple is absent.
6. Add a second recipe sharing an ingredient → confirm one merged line with the
   summed quantity and both recipe names underneath.
7. Check items off → they sink into folds. **Finish trip** → checked ones vanish,
   unchecked survive, meal strip persists.
8. Remove a recipe from the meal strip → its ingredients withdraw, shared lines
   drop to the other recipe's amount rather than disappearing.

- [ ] **Step 3: Commit**

```bash
git add templates/recipes.html
git commit -m "feat(recipes): pantry filing, recipe detail, and the editor"
```

---

## Task 14: Full verification and deploy

- [ ] **Step 1: Run the whole suite**

Run: `./.venv/bin/pytest tests/ -v`
Expected: PASS. Every soundboard test must still be green — if any regressed,
the blueprint registration is interfering and that must be fixed before deploy.

- [ ] **Step 2: Confirm the soundboard is untouched**

Open `http://127.0.0.1:5050/` and play a sound. The recipes feature must be
invisible from the soundboard, and vice versa.

- [ ] **Step 3: Check the diff for accidents**

```bash
git diff main...recipes --stat
```

Expected: `recipes/*`, `templates/recipes.html`, `tests/*`, `docs/*`, and exactly
two added lines in `server.py`. The user's uncommitted `server.py` and
`templates/index.html` work must not appear.

- [ ] **Step 4: Deploy**

**Ask the user before running this** — it touches their live site.

```bash
rsync -av --exclude data --exclude .venv --exclude .superpowers \
  ~/Documents/GitHub/SounderServer/ sound@<VPS_IP>:/home/sound/sound-server/
ssh sound@<VPS_IP> 'sudo systemctl restart soundserver'
```

`data/` is excluded, so `recipes.db` is created fresh on the box at first boot
and seeded with the store layout automatically.

- [ ] **Step 5: Verify in production**

Open `https://new.sounderserver.party/recipes/` on a phone. Log in so writes are
allowed. Add one recipe, add it to the list, check an item off from a second
phone.

- [ ] **Step 6: Merge**

```bash
git checkout main && git merge --no-ff recipes
```

---

## Self-Review

**Spec coverage** — every requirement maps to a task:

| Spec requirement | Task |
|---|---|
| Five sections + Unsorted, hidden sub-order, alphabetical within | 1 |
| Shared live list, multiple writers | 1 (WAL), 8 (poll), 11 |
| Hybrid freeform + optional pantry link | 1, 6 |
| Quantity merging; no cross-family guessing | 2, 7 |
| Purchase unit from the Shaws product | 5, 7, 11 |
| Transient uncheck at add-time | 12 |
| Staples flag, folded on the add sheet | 5, 12 |
| No inventory tracking | *(absent by design)* |
| Checked items sink into a per-section fold | 11 |
| Additive list, Finish trip clears only checked | 7 |
| Meal-plan strip; removing a recipe withdraws only its share | 8, 11 |
| Dense-row browse, ⊕ queueing, no photo required | 12 |
| Blueprint at `/recipes`, two lines in `server.py` | 4 |
| SQLite, not JSON | 1 |
| Reads open, writes behind `can_edit()` | 4, 5 |
| Identity from `localStorage.ss_name` | 10 |
| Shaws export, server never drives a browser | 9 |
| Recipe import endpoint + manual form | 6, 13 |
| Parser and unit tests | 2, 3 |
| API lifecycle tests | 7, 8 |

**Known rough edges**, deliberately accepted for v1 and worth revisiting once
it has been used for real:

- `editPantry()` uses `prompt()`/`confirm()` chains. Functional and fast to
  build, but ugly on a phone and it cannot reorder sections. Replace with a real
  sheet once you know how often the Pantry tab actually gets opened. **Note:**
  browser dialogs block the page — do not drive this screen with automated
  browser tooling.
- Section and sub-category reordering is seeded and editable only in SQL. The
  spec calls for in-app reordering; it is not in this plan because the user said
  the layout is right and can change over time. Add it when it first annoys
  someone.
- Recipe photos are stored as a URL and never displayed. Rows show a placeholder
  glyph. This matches the "no photo required" decision.
- The parser handles English written recipes. Anything pasted from a PDF with
  hard line wraps will parse badly; the raw line still survives.

