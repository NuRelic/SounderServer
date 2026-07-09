# A sound uploaded/fetched while the server is already running is in _LIBRARY
# (scan_library) but has no catalog row yet — catalog_sync() only runs at startup.
# record_play must still count it instead of silently dropping the play.

def test_record_play_counts_sound_added_after_startup(app):
    assert app._FILE2ID.get("short_sound.wav") is None      # no catalog id yet
    app.record_play("short_sound.wav")
    app.record_play("short_sound.wav")
    assert app._PLAYS.get("short_sound.wav") == 2           # counted, not dropped
    assert app._FILE2ID.get("short_sound.wav") is not None  # self-healed a catalog id


def test_fire_counts_uncatalogued_sound_for_every_user(client, app):
    client.post("/api/fire", json={"file": "short_sound.wav", "user": "alice"})
    client.post("/api/fire", json={"file": "short_sound.wav", "user": "bob"})
    assert app._PLAYS.get("short_sound.wav") == 2           # both users' plays land

    row = next(s for s in client.get("/api/sounds").get_json()["sounds"]
               if s["file"] == "short_sound.wav")
    assert row["plays"] == 2                                # and /api/sounds reports it


def test_record_play_ignores_unknown_file(app):
    app.record_play("does_not_exist.wav")                   # not in library -> no-op
    assert app._PLAYS.get("does_not_exist.wav") is None
