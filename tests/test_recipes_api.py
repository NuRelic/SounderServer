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
