import importlib
import sqlite3

import pytest


def _api():
    """The live blueprint module — the same object the test client is serving.

    conftest scrubs `recipes.*` from sys.modules per test, so this has to be
    imported after the app fixture has run, not at module scope.
    """
    return importlib.import_module("recipes.api")


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


def test_sections_carry_their_subcategories_in_order(recipes_client):
    """The filing screen builds its picker from this, so it must be complete."""
    sections = recipes_client.get("/recipes/api/sections").get_json()["sections"]
    produce = sections[0]
    assert produce["subsections"] == ["produce", "fancy cheese"]
    everything = [sub for s in sections for sub in s["subsections"]]
    assert "spices" in everything and "dairy" in everything
    assert all(s["subsections"] for s in sections)


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


def test_singular_ingredient_finds_the_plural_pantry_item(recipes_client):
    """"1 onion, diced" must land on the existing "Onions", not beside it.

    This is the most ordinary input there is; splitting it defeats merging.
    """
    item = _mk_pantry(recipes_client, "Onions", subsection="produce")
    resolved = recipes_client.get(
        "/recipes/api/pantry/resolve?name=onion").get_json()
    assert resolved["id"] == item["id"]


def test_plural_ingredient_finds_the_singular_pantry_item(recipes_client):
    item = _mk_pantry(recipes_client, "Tomato", subsection="produce")
    resolved = recipes_client.get(
        "/recipes/api/pantry/resolve?name=tomatoes").get_json()
    assert resolved["id"] == item["id"]


def test_plural_match_does_not_create_a_second_pantry_row(recipes_client):
    _mk_pantry(recipes_client, "Onions", subsection="produce")
    recipes_client.post("/recipes/api/recipes", json={
        "name": "Soup", "who": "brandon",
        "ingredients": ["1 onion, diced"],
    })
    names = [i["name"] for i in
             recipes_client.get("/recipes/api/pantry").get_json()["items"]]
    assert names.count("Onions") == 1
    assert "onion" not in [n.lower() for n in names if n != "Onions"]


def test_an_exact_name_always_beats_a_plural_variant(recipes_client):
    """If the pantry genuinely holds both spellings, neither is hijacked.

    The create route can no longer produce this state on purpose -- asking it
    for "Green" when "Greens" exists now hands back "Greens", which is the
    whole point of the fix -- so the pair is seeded straight into the table.
    """
    api = _api()
    plural = _mk_pantry(recipes_client, "Greens", subsection="produce")
    cur = api._conn().execute("INSERT INTO pantry_item(name) VALUES('Green')")
    api._conn().commit()
    single_id = cur.lastrowid
    for name, expected in (("Greens", plural["id"]), ("Green", single_id)):
        got = recipes_client.get(
            f"/recipes/api/pantry/resolve?name={name}").get_json()
        assert got["id"] == expected


@pytest.mark.parametrize("stored, looked_up", [
    ("bas", "bass"),            # a doubled s is not a plural
    ("bass", "bas"),            # ...in either direction
    ("molasse", "molasses"),    # -sses gives up the whole "es", never just "s"
    ("ba", "bay"),              # too short to vary at all
    ("rice", "ice"),            # not a plural relationship in any direction
])
def test_different_groceries_are_never_collapsed(recipes_client, stored,
                                                 looked_up):
    _mk_pantry(recipes_client, stored)
    resp = recipes_client.get(f"/recipes/api/pantry/resolve?name={looked_up}")
    assert resp.status_code == 404, f"{looked_up!r} wrongly matched {stored!r}"


def test_losing_the_create_race_twice_reraises_the_real_error(recipes_client,
                                                             monkeypatch):
    """The IntegrityError fallback must surface the collision, not a bare raise.

    Stubbing resolve_pantry to always miss reproduces the pathological case:
    the INSERT collides with an existing row, and the re-resolve after it comes
    back empty too. A bare `raise` there has no active exception and turns a
    real IntegrityError into a confusing RuntimeError.
    """
    api = _api()
    _mk_pantry(recipes_client, "Shallots")
    monkeypatch.setattr(api, "resolve_pantry", lambda name: None)
    with pytest.raises(sqlite3.IntegrityError):
        api.get_or_create_pantry("Shallots")


def test_writes_require_login(reader_client):
    resp = reader_client.post("/recipes/api/pantry",
                              json={"name": "Sneaky", "who": "nobody"})
    assert resp.status_code == 403


def test_reads_do_not_require_login(reader_client):
    assert reader_client.get("/recipes/api/pantry").status_code == 200


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


def test_prep_is_stored_at_write_time_not_reparsed_on_read(recipes_client,
                                                           monkeypatch):
    """A saved recipe's prep must not drift when parse.py's heuristics change."""
    api = _api()
    r = _mk_recipe(recipes_client)
    monkeypatch.setattr(api, "parse_ingredient", lambda line: {
        "raw": line, "qty": None, "unit": None, "name": line,
        "prep": "DRIFTED", "note": "",
    })
    detail = recipes_client.get(f"/recipes/api/recipes/{r['id']}").get_json()
    by_raw = {i["raw_text"]: i for i in detail["ingredients"]}
    assert by_raw["2 yellow onions, diced"]["prep"] == "diced"


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


def test_lines_within_a_section_follow_the_hidden_sub_order(recipes_client):
    """The sub-categories are invisible, but they are the whole point of the walk.

    Coffee (coffee & tea) sits before the spices, which sit before baking, even
    though a plain alphabetical sort would put Flour between Cumin and Paprika.
    Only the two spices tie on sub-order, and there alphabetical breaks it.
    """
    for name, sub in [("Paprika", "spices"), ("Flour", "baking"),
                      ("Cumin", "spices"), ("Coffee", "coffee & tea")]:
        _mk_pantry(recipes_client, name, subsection=sub)
        recipes_client.post("/recipes/api/list/add",
                            json={"name": name, "who": "kate"})
    sections = recipes_client.get("/recipes/api/list").get_json()["sections"]
    early = [s for s in sections if s["name"] == "Early Aisles"][0]
    assert [l["name"] for l in early["lines"]] == [
        "Coffee", "Cumin", "Paprika", "Flour"]


def test_the_flat_line_list_is_itself_in_walking_order(recipes_client):
    """`lines` is not an unordered bag — it is the walk, start to finish.

    Adding them backwards proves the order comes from the store layout rather
    than from insertion order.
    """
    _mk_pantry(recipes_client, "Bagels", subsection="bread")
    _mk_pantry(recipes_client, "Trash bags", subsection="home")
    _mk_pantry(recipes_client, "Apples", subsection="produce")
    for name in ["Bagels", "Trash bags", "Apples"]:
        recipes_client.post("/recipes/api/list/add",
                            json={"name": name, "who": "kate"})
    recipes_client.post("/recipes/api/list/add",
                        json={"name": "Birthday candles", "who": "kate"})
    assert [l["name"] for l in _lines(recipes_client)] == [
        "Apples", "Trash bags", "Bagels", "Birthday candles"]


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


def test_clear_takes_unchecked_lines_too(recipes_client):
    """The difference from finish-trip: nothing survives for not being in the cart."""
    recipes_client.post("/recipes/api/list/add", json={"name": "Milk", "who": "k"})
    recipes_client.post("/recipes/api/list/add", json={"name": "Capers", "who": "k"})
    milk = [l for l in _lines(recipes_client) if l["name"] == "Milk"][0]
    recipes_client.post(f"/recipes/api/list/line/{milk['id']}/check",
                        json={"checked": True, "who": "brandon"})
    recipes_client.post("/recipes/api/list/clear", json={"who": "brandon"})
    assert _lines(recipes_client) == []


def test_clear_keeps_the_meal_strip(recipes_client):
    chili = _mk_recipe(recipes_client)
    _add_to_list(recipes_client, chili["id"])
    recipes_client.post("/recipes/api/list/clear", json={"who": "brandon"})
    data = recipes_client.get("/recipes/api/list").get_json()
    assert [m["name"] for m in data["meals"]] == ["Black Bean Chili"]


def test_clear_bumps_the_version_so_the_other_phone_sees_it(recipes_client):
    recipes_client.post("/recipes/api/list/add", json={"name": "Milk", "who": "k"})
    before = recipes_client.get("/recipes/api/list").get_json()["version"]
    recipes_client.post("/recipes/api/list/clear", json={"who": "brandon"})
    poll = recipes_client.get(f"/recipes/api/list/poll?since={before}").get_json()
    assert poll["changed"] is True


def test_a_recipe_can_go_back_on_the_list_after_a_clear(recipes_client):
    """Its meal_plan row is still there, so re-adding has to work anyway."""
    chili = _mk_recipe(recipes_client)
    _add_to_list(recipes_client, chili["id"])
    recipes_client.post("/recipes/api/list/clear", json={"who": "brandon"})
    _add_to_list(recipes_client, chili["id"])
    assert "yellow onions" in [l["name"] for l in _lines(recipes_client)]


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


def test_removing_a_recipe_only_deletes_its_own_contribution_rows(recipes_client):
    """Guards the WHERE recipe_id=? filter itself.

    Two recipes on the list, remove one: the survivor's own contribution row
    for a *different* ingredient (rice) must still be intact, and the shared
    onions line must keep exactly the surviving recipe's contribution — not
    zero, not both.
    """
    chili = _mk_recipe(recipes_client)
    fry = _mk_recipe(recipes_client, STIR_FRY)
    _add_to_list(recipes_client, chili["id"])
    _add_to_list(recipes_client, fry["id"])
    recipes_client.post(f"/recipes/api/list/remove-recipe/{chili['id']}",
                        json={"who": "brandon"})
    lines = _lines(recipes_client)
    onions = [l for l in lines if l["name"] == "yellow onions"][0]
    assert onions["sources"] == ["Veggie Stir Fry"]
    rice = [l for l in lines if l["name"] == "rice"][0]
    assert rice["sources"] == ["Veggie Stir Fry"]


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


def test_export_needs_product_link_excludes_items_that_have_one(recipes_client):
    """Guards the `if not line['shaws_url']` filter itself.

    With one item linked and one unlinked, needs_product_link must name only
    the unlinked one — a filter that always appended (or never appended)
    would still pass a test that only checked the linked item's own fields.
    """
    milk = _mk_pantry(recipes_client, "Milk", subsection="dairy")
    recipes_client.patch(f"/recipes/api/pantry/{milk['id']}", json={
        "shaws_url": "https://shaws.com/p/whole-milk-gal", "who": "brandon"})
    recipes_client.post("/recipes/api/list/add", json={"name": "Milk", "who": "k"})
    recipes_client.post("/recipes/api/list/add",
                        json={"name": "Birthday candles", "who": "sam"})
    export = recipes_client.get("/recipes/api/list/export").get_json()
    assert export["needs_product_link"] == ["Birthday candles"]
