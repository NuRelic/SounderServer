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
