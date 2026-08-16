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
    # server.py imports the recipes and lamulana blueprints, and both
    # recipes/api.py and lamulana/api.py resolve DB_PATH and run the
    # schema/seed at import time against DATA_DIR. If a previous test left
    # recipes.* or lamulana.* cached in sys.modules, re-importing server
    # would just reuse that stale module (and its DB_PATH pointing at a
    # different tmp_path's database) instead of picking up the DATA_DIR set
    # above. Scrub both so each test's server import gets fresh, matching
    # databases. Each package's db.py keeps its per-thread connection cache on
    # the module object and keys it by path, so it cannot survive this either
    # way.
    for mod in [m for m in sys.modules
                if m in ("recipes", "lamulana")
                or m.startswith(("recipes.", "lamulana."))]:
        del sys.modules[mod]
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
def recipes_db(tmp_path):
    """A fresh, seeded recipes database on disk, isolated per test.

    recipes.db holds no DATA_DIR-derived module-level state (unlike server.py),
    so unlike the `app` fixture above there is nothing to reset by popping it
    from sys.modules and reimporting — doing so would only hand back a second,
    distinct module object while tests/test_recipes_db.py's own top-level
    `import recipes.db as db` keeps pointing at the first. Import normally so
    both references resolve to the same cached module; isolation comes from
    handing each test its own on-disk database file instead.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    db = importlib.import_module("recipes.db")
    conn = db.connect(str(tmp_path / "recipes.db"))
    db.init_schema(conn)
    db.seed_sections(conn)
    yield conn
    conn.close()


@pytest.fixture
def lamulana_db(tmp_path):
    """A fresh, seeded lamulana database on disk, isolated per test.

    Same reasoning as `recipes_db` above: import normally so this module object
    is the one the test file's own `import lamulana.db` also resolves to, and
    get isolation from a per-test database file instead.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    db = importlib.import_module("lamulana.db")
    conn = db.connect(str(tmp_path / "lamulana.db"))
    db.init_schema(conn)
    db.seed_all(conn)
    yield conn
    conn.close()


@pytest.fixture
def recipes_client(app):
    """Test client with edit rights, against the real app + blueprint."""
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["can_edit"] = True
    return c


@pytest.fixture
def reader_client(app):
    """Test client with no edit rights — for asserting writes are gated."""
    return app.app.test_client()
