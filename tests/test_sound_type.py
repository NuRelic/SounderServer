def test_is_long_defaults_to_duration(app):
    assert app.is_long("long_song.wav") is True      # ~20s
    assert app.is_long("short_sound.wav") is False   # ~1s

def test_override_forces_type(app):
    app._TYPE_OVERRIDE["short_sound.wav"] = "song"
    app._TYPE_OVERRIDE["long_song.wav"] = "sound"
    assert app.is_long("short_sound.wav") is True
    assert app.is_long("long_song.wav") is False

def test_sound_type_endpoint_requires_edit(client):
    r = client.post("/api/sound_type", json={"file": "short_sound.wav", "type": "song"})
    assert r.status_code == 403

def test_sound_type_endpoint_sets_and_clears(editor_client, app):
    r = editor_client.post("/api/sound_type", json={"file": "short_sound.wav", "type": "song"})
    assert r.get_json()["long"] is True
    assert app._TYPE_OVERRIDE.get("short_sound.wav") == "song"
    r = editor_client.post("/api/sound_type", json={"file": "short_sound.wav", "type": "auto"})
    assert r.get_json()["long"] is False
    assert "short_sound.wav" not in app._TYPE_OVERRIDE

def test_sound_type_rejects_bad_type(editor_client):
    r = editor_client.post("/api/sound_type", json={"file": "short_sound.wav", "type": "nope"})
    assert r.status_code == 400

def test_api_sounds_reflects_override(editor_client):
    editor_client.post("/api/sound_type", json={"file": "short_sound.wav", "type": "song"})
    sounds = editor_client.get("/api/sounds").get_json()["sounds"]
    row = next(s for s in sounds if s["file"] == "short_sound.wav")
    assert row["long"] is True
