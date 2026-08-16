import importlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lamulana import seed


def test_every_area_is_named_once():
    names = [a for a in seed.AREAS]
    assert len(names) == 28
    assert len(set(names)) == 28


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
