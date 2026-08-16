import json


def test_page_renders(client):
    r = client.get("/lamulana/")
    assert r.status_code == 200


def test_bootstrap_returns_areas_and_checklist(client):
    r = client.get("/lamulana/api/bootstrap")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["areas"]) == 28
    assert data["areas"][0]["name"] == "Village of Departure"
    groups = {g["group"]: g for g in data["checklist"]}
    assert set(groups) == {"Guardians", "Sacred Orbs", "Mantras", "Maps", "Apps"}
    assert len(groups["Guardians"]["items"]) == 10


def test_bootstrap_counts_start_at_zero(client):
    counts = client.get("/lamulana/api/bootstrap").get_json()["counts"]
    assert counts == {"clues": 0, "clues_understood": 0, "threads_open": 0}
