def test_rename_carries_play_stats(editor_client, app):
    app.catalog_sync()                          # give library files catalog ids
    app.record_play("short_sound.wav")
    app.record_play("short_sound.wav")
    assert app._PLAYS.get("short_sound.wav") == 2

    r = editor_client.post("/api/rename",
                           json={"file": "short_sound.wav", "name": "renamed_sound"})
    assert r.get_json()["ok"] is True

    # stats follow the soundid to the new filename, not reset to 0
    assert app._PLAYS.get("renamed_sound.wav") == 2
    assert app._PLAYS.get("short_sound.wav") is None

    row = next(s for s in editor_client.get("/api/sounds").get_json()["sounds"]
               if s["file"] == "renamed_sound.wav")
    assert row["plays"] == 2
