"""Default store layout — the order of these lists is the order we walk Shaws.

Sections are visible headers. Sub-categories are invisible sort keys: they exist
so items land in the right part of a section without adding headers to scroll
past. Both are reorderable in the app; this is a first-run seed, not a constant.

`seed_sections()` inserts this with `INSERT OR IGNORE`, so it only ever takes
effect on the very first boot of a database — editing SECTIONS afterward is a
silent no-op against existing databases. That's intentional: it's what lets
the family reorder sections/subsections in the app without a later deploy
stomping their changes back to this list. It does mean this file stops being
"the" source of truth for anyone who has already booted once; treat it as the
seed, not a live config.
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
