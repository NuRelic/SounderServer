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


@pytest.mark.parametrize("pairs,expected", [
    # 3 tsp + 1 gallon = 771 tsp, which is unreadable — step up to the next
    # present unit (gallon) instead of reporting "771 tsp".
    ([(3, "tsp"), (1, "gallon")], [(1, "gallon")]),
    # 1 ml + 1 cup: ml gives 237.6, over the cap — step up to cup.
    ([(1, "ml"), (1, "cup")], [(1, "cup")]),
    # oz already renders readably (16), so a zero-quantity lb alongside it
    # must not change the chosen unit.
    ([(16, "oz"), (0, "lb")], [(16, "oz")]),
])
def test_merge_steps_up_to_a_readable_present_unit(pairs, expected):
    assert units.merge(pairs) == expected


def test_format_quantity_pluralizes_zero():
    # English pluralizes at zero: "0 cups", not "0 cup".
    assert units.format_quantity(0, "cup") == "0 cups"
