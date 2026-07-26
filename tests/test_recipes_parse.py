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
