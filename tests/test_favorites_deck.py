def test_norm_fav_migrates_list(app):
    rec = app._norm_fav(["a.wav", "b.wav", "a.wav"])
    assert rec == {"favs": ["a.wav", "b.wav"], "deck": []}

def test_norm_fav_keeps_dict_and_filters_deck(app):
    rec = app._norm_fav({"favs": ["a.wav", "b.wav"], "deck": ["b.wav", "zzz.wav"]})
    assert rec["favs"] == ["a.wav", "b.wav"]
    assert rec["deck"] == ["b.wav"]            # zzz not a fav -> dropped

def test_favorite_adds_to_favs_not_deck(client, app):
    client.post("/api/favorite", json={"file": "short_sound.wav", "on": True, "user": "tester"})
    rec = app._FAVS_BY_USER[app.fav_key("tester")]
    assert "short_sound.wav" in rec["favs"]
    assert rec["deck"] == []

def test_unfavorite_purges_deck(client, app):
    client.post("/api/favorite", json={"file": "short_sound.wav", "on": True, "user": "tester"})
    app._FAVS_BY_USER[app.fav_key("tester")]["deck"] = ["short_sound.wav"]
    client.post("/api/favorite", json={"file": "short_sound.wav", "on": False, "user": "tester"})
    rec = app._FAVS_BY_USER[app.fav_key("tester")]
    assert "short_sound.wav" not in rec["favs"]
    assert "short_sound.wav" not in rec["deck"]

def test_deck_setter_keeps_only_favorites(client, app):
    client.post("/api/favorite", json={"file": "short_sound.wav", "on": True, "user": "tester"})
    client.post("/api/favorite", json={"file": "long_song.wav", "on": True, "user": "tester"})
    r = client.post("/api/favorites/order",
                    json={"order": ["long_song.wav", "ghost.wav"], "user": "tester"})
    assert r.get_json()["deck"] == ["long_song.wav"]

def test_favorites_get_shape(client, app):
    client.post("/api/favorite", json={"file": "short_sound.wav", "on": True, "user": "tester"})
    r = client.get("/api/favorites?user=tester").get_json()
    assert set(r.keys()) >= {"favs", "deck"}
    assert "short_sound.wav" in r["favs"]
