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
