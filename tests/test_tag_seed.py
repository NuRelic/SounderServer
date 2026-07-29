"""Tag seed derivation — the one-shot import that bootstraps data/tags.json.

The rules here were derived from a live pull of the 1,994-clip library; the
cases below are the real ones that shaped them, not invented examples.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from seed_tags import PARENTS, derive


def names(*n):
    return list(n)


def test_groups_below_threshold_are_ignored():
    # Two clips sharing a prefix is a coincidence, not a tag.
    got = derive(names("zz_one", "zz_two"))
    assert got["tags"] == {}
    assert got["assign"] == {}


def test_group_of_three_becomes_a_tag():
    got = derive(names("zz_one", "zz_two", "zz_three"))
    assert "zz" in got["tags"]
    assert set(got["assign"]) == {"zz_one", "zz_two", "zz_three"}


def test_sentence_starters_are_rejected():
    # "the_call", "the_top_alt", "the_universe_1" share a prefix but "the" is
    # just the first word of a phrase.
    got = derive(names("the_call", "the_top_alt", "the_universe_1", "the_universe_2"))
    assert "the" not in got["tags"]


def test_hm_is_not_treated_as_a_filler_word():
    # Regression: an early stop-word list ate `hm` (Hotline Miami soundtrack)
    # because "hm" reads as a filler noise. It is a real tag.
    got = derive(names("hm_crystals", "hm_flatline", "hm_deep_cover"))
    assert "hm" in got["tags"]


def test_clips_land_on_child_tags_not_the_parent():
    got = derive(names("dccd_my_butt", "dccd_smack_talk", "dccd_yeahno"))
    assert got["assign"]["dccd_my_butt"] == ["dccd"]
    assert got["tags"]["dccd"]["parent"] == "dcc"


def test_parent_tag_is_created_for_its_children():
    got = derive(names("dccd_a", "dccd_b", "dccd_c"))
    assert "dcc" in got["tags"]
    assert "parent" not in got["tags"]["dcc"]


def test_bare_parent_clip_is_assigned_even_below_threshold():
    # dcc_class_selection is a group of one, so the size-3 rule drops it — but
    # its prefix is a parent slug, so it belongs directly on the parent.
    got = derive(names("dccd_a", "dccd_b", "dccd_c", "dcc_class_selection"))
    assert got["assign"]["dcc_class_selection"] == ["dcc"]


def test_bare_parent_clip_alone_creates_nothing():
    # No children means no parent tag to hang it on.
    got = derive(names("dcc_class_selection"))
    assert got["tags"] == {}


def test_every_parent_in_the_map_is_reachable():
    # Guards against a typo in PARENTS silently orphaning a child slug.
    for child, parent in PARENTS.items():
        assert parent != child
        assert parent not in PARENTS, f"{parent} is both a parent and a child"


def test_labels_fall_back_to_the_slug():
    got = derive(names("qqq_one", "qqq_two", "qqq_three"))
    assert got["tags"]["qqq"]["label"] == "qqq"


def test_known_slugs_get_friendly_labels():
    got = derive(names("dccd_a", "dccd_b", "dccd_c"))
    assert got["tags"]["dccd"]["label"] == "Donut"
    assert got["tags"]["dcc"]["label"] == "Dungeon Crawler Carl"


def test_case_is_normalised_on_the_prefix():
    got = derive(names("Fortune_a", "fortune_b", "FORTUNE_c"))
    assert list(got["tags"]) == ["fortune"]
    assert len(got["assign"]) == 3


def test_names_without_underscores_are_untouched():
    got = derive(names("Barb", "laugh4", "lenny", "llama"))
    assert got["tags"] == {}


def test_numbered_prefixes_group_by_their_stem():
    # e17_/e18_/e20_ are each a group of one, so the size rule drops them — but
    # they're one family with the number in the prefix. This is the live
    # EPIC: The Musical rename (epic_17_x -> e17_x).
    got = derive(names("e17_other_ways", "e18_underworld", "e20_monster"))
    assert "e" in got["tags"]
    assert got["assign"]["e17_other_ways"] == ["e"]


def test_stem_grouping_needs_three_distinct_numbers():
    got = derive(names("q1_a", "q2_b"))
    assert got["tags"] == {}


def test_repeats_of_one_number_are_not_a_family():
    # q1_a/q1_b/q1_c is a normal prefix group, not a numbered family.
    got = derive(names("q1_a", "q1_b", "q1_c"))
    assert list(got["tags"]) == ["q1"]


def test_all_digit_prefixes_do_not_produce_an_empty_stem():
    got = derive(names("2001_error", "1979_x", "9000_y"))
    assert "" not in got["tags"]


def test_stem_grouping_does_not_fight_the_curated_parents():
    # p3/p4/p5 are real tags in their own right and are parented by hand;
    # they must not collapse into a "p" stem tag.
    got = derive(names(*[f"p3_{i}" for i in range(3)],
                       *[f"p4_{i}" for i in range(3)],
                       *[f"p5_{i}" for i in range(3)]))
    assert "p" not in got["tags"]
    assert got["tags"]["p3"]["parent"] == "persona"


def test_assignments_are_lists_so_a_clip_can_carry_several_tags():
    got = derive(names("zz_one", "zz_two", "zz_three"))
    assert all(isinstance(v, list) for v in got["assign"].values())


def test_assign_is_keyed_by_filename_not_display_name():
    # Regression: keying by name meant every entry looked like a ghost against
    # _LIBRARY (which is keyed by filename) and filtered to nothing, so the
    # board showed 58 tags with a count of 0 on every card.
    got = derive([
        {"name": "zz_one", "file": "zz_one.wav"},
        {"name": "zz_two", "file": "zz_two.mp3"},
        {"name": "zz_three", "file": "zz_three.wav"},
    ])
    assert set(got["assign"]) == {"zz_one.wav", "zz_two.mp3", "zz_three.wav"}


def test_bare_parent_sweep_is_keyed_by_filename_too():
    got = derive([
        {"name": "dccd_a", "file": "dccd_a.wav"},
        {"name": "dccd_b", "file": "dccd_b.wav"},
        {"name": "dccd_c", "file": "dccd_c.wav"},
        {"name": "dcc_class_selection", "file": "dcc_class_selection.mp3"},
    ])
    assert got["assign"]["dcc_class_selection.mp3"] == ["dcc"]


# ---- merge mode -----------------------------------------------------------
# The seed refuses to overwrite an existing tags.json, so it cannot be re-run
# as the library grows. merge_into() adds what is new without ever disturbing
# curation that has already happened by hand.

from seed_tags import merge_into


def test_merge_adds_a_newly_matching_clip():
    store = {"tags": {"zz": {"label": "ZZ"}}, "assign": {"zz_one.wav": ["zz"]}}
    items = [{"name": f"zz_{i}", "file": f"zz_{i}.wav"} for i in ("one", "two", "three")]
    out, added = merge_into(store, items)
    assert added == 2
    assert set(out["assign"]) == {"zz_one.wav", "zz_two.wav", "zz_three.wav"}


def test_merge_never_touches_a_hand_edited_label():
    store = {"tags": {"mew": {"label": "Mewgenics"}}, "assign": {}}
    items = [{"name": f"mew_{i}", "file": f"mew_{i}.wav"} for i in range(3)]
    out, _ = merge_into(store, items)
    assert out["tags"]["mew"]["label"] == "Mewgenics"      # not reset to the seed label


def test_merge_never_moves_an_already_assigned_clip():
    store = {"tags": {"zz": {"label": "ZZ"}, "other": {"label": "Other"}},
             "assign": {"zz_one.wav": ["other"]}}
    items = [{"name": f"zz_{i}", "file": f"zz_{i}.wav"} for i in ("one", "two", "three")]
    out, _ = merge_into(store, items)
    assert out["assign"]["zz_one.wav"] == ["other"]        # curation wins


def test_merge_preserves_an_existing_parent():
    store = {"tags": {"hm": {"label": "Hotline Miami", "parent": "hotline"},
                      "hotline": {"label": "Hotline Miami"}}, "assign": {}}
    items = [{"name": f"hm_{i}", "file": f"hm_{i}.wav"} for i in range(3)]
    out, _ = merge_into(store, items)
    assert out["tags"]["hm"]["parent"] == "hotline"


def test_merge_is_idempotent():
    store = {"tags": {}, "assign": {}}
    items = [{"name": f"zz_{i}", "file": f"zz_{i}.wav"} for i in range(3)]
    once, a1 = merge_into(store, items)
    twice, a2 = merge_into(once, items)
    assert a2 == 0 and once == twice


def test_merge_does_not_resurrect_a_deleted_tag():
    # 'butt' was deliberately dropped; re-running the seed must not bring it back
    store = {"tags": {}, "assign": {}, "retired": ["butt"]}
    items = [{"name": f"butt_{i}", "file": f"butt_{i}.wav"} for i in range(3)]
    out, added = merge_into(store, items)
    assert "butt" not in out["tags"] and added == 0
