import os, sys, wave, struct, importlib, pathlib
import pytest

def _write_wav(path, seconds):
    with wave.open(str(path), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(struct.pack("<" + "h" * int(8000 * seconds), *([0] * int(8000 * seconds))))

@pytest.fixture
def app(tmp_path, monkeypatch):
    data = tmp_path / "data"; sounds = tmp_path / "sounds"
    data.mkdir(); sounds.mkdir()
    _write_wav(sounds / "short_sound.wav", 1.0)     # ~1s  -> sound
    _write_wav(sounds / "long_song.wav", 20.0)      # ~20s -> song (>15s)
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SOUND_DIR", str(sounds))
    monkeypatch.setenv("USER_PASS", "editpw")
    monkeypatch.setenv("ADMIN_PASS", "adminpw")
    monkeypatch.setenv("CATALOG_SEED", str(tmp_path / "nonexistent.db"))
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    if "server" in sys.modules:
        del sys.modules["server"]
    server = importlib.import_module("server")
    server.scan_library()        # populate _LIBRARY (only runs in __main__ otherwise)
    server.app.config["TESTING"] = True
    return server

@pytest.fixture
def client(app):
    return app.app.test_client()

@pytest.fixture
def editor_client(app):
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["can_edit"] = True
    return c

@pytest.fixture
def recipes_db(tmp_path, monkeypatch):
    """A fresh, seeded recipes database on disk, isolated per test."""
    import importlib, sys
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    for mod in ("recipes.db", "recipes.api", "recipes", "recipes.seed"):
        sys.modules.pop(mod, None)
    db = importlib.import_module("recipes.db")
    conn = db.connect(str(tmp_path / "recipes.db"))
    db.init_schema(conn)
    db.seed_sections(conn)
    return conn
