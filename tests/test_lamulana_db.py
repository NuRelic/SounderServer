from lamulana import seed


def test_every_area_is_named_once():
    assert len(seed.AREAS) == 28
    assert len(set(seed.AREAS)) == len(seed.AREAS)


def test_areas_include_both_halves_of_the_game():
    # Eg-Lana's own fields and the La-Mulana ruins revisited later both count.
    assert "Immortal Battlefield" in seed.AREAS
    assert "Gate of Guidance" in seed.AREAS


def test_checklist_groups_have_unique_names_within_a_group():
    for group, items in seed.CHECKLIST:
        assert len(set(items)) == len(items), f"duplicate row in {group}"


def test_checklist_group_sizes():
    sizes = {group: len(items) for group, items in seed.CHECKLIST}
    assert sizes == {
        "Guardians": 10,
        "Sacred Orbs": 10,
        "Mantras": 10,
        "Maps": 16,
        "Apps": 24,
    }


def test_non_ascii_row_names_are_intact():
    # A bad re-encode mangles these silently -- every structural assertion
    # above still passes on "MÃ³Ã°ir". The em-dash matters too: it separates
    # the name from the location in every Guardian and Mantra row.
    mantras = dict(seed.CHECKLIST)["Mantras"]
    assert "Iorð — Annwfn (D-4)" in mantras
    assert "Sær — Shrine of the Frost Giants (C-3)" in mantras
    assert "Móðir — Eternal Prison - Gloom (C-5)" in mantras
