def test_library_scanned(app):
    assert "short_sound.wav" in app._LIBRARY
    assert "long_song.wav" in app._LIBRARY
