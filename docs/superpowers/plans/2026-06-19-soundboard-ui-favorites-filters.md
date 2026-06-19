# Favorites Deck, Filters & Header UX — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-clip song/sound override, a Favorites "deck" model, a duration filter, Excel-style sort buttons, a combined/denser header, and remove the NSFW gate — per `docs/superpowers/specs/2026-06-19-soundboard-ui-favorites-filters-design.md`.

**Architecture:** Single Flask app (`server.py`, module-level state) + one server-rendered template (`templates/index.html`, vanilla JS). Backend changes get pytest unit/integration tests (new harness). Frontend is vanilla JS with no test runner, so frontend tasks are verified by running the app and observing (steps say exactly what to check). The validated look/markup for new UI lives in the mockup `~/dcc-clips/mock/mockup.html` (built from the app's real CSS) — copy CSS/HTML from there.

**Tech Stack:** Python 3 / Flask 3.1, pytest (new), vanilla JS + Canvas, ffmpeg/ffprobe (already used).

**Conventions to match:** JSON stores via `_load(path, default)` / `_save(path, obj)`; per-`data/`-file state loaded at import; endpoints return `jsonify({"ok": ...})`; edit gate `can_edit()`. `data/` is gitignored and excluded from deploy rsync — new `data/type_overrides.json` is runtime state, not committed.

---

## Task 1: pytest harness

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/__init__.py` (empty)
- Modify: `requirements.txt` (add `pytest`)

- [ ] **Step 1: Add pytest to requirements**

Append to `requirements.txt`:
```
pytest>=8.0
```
Install: `./.venv/bin/pip install pytest`

- [ ] **Step 2: Create the empty package marker**

Create `tests/__init__.py` with no content.

- [ ] **Step 3: Write the conftest fixtures**

Create `tests/conftest.py`. It points the app at a temp `DATA_DIR`/`SOUND_DIR` **before** importing `server`, drops two dummy clips in the library (a short "sound" and a long "song"), scans them, and yields a Flask test client plus helpers.

```python
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
```

- [ ] **Step 4: Verify the harness imports and scans**

Create a temporary check `tests/test_harness.py`:
```python
def test_library_scanned(app):
    assert "short_sound.wav" in app._LIBRARY
    assert "long_song.wav" in app._LIBRARY
```
Run: `./.venv/bin/python -m pytest tests/test_harness.py -v`
Expected: PASS (2 files present).

- [ ] **Step 5: Commit**
```bash
git add requirements.txt tests/__init__.py tests/conftest.py tests/test_harness.py
git commit -m "test: add pytest harness with temp data/sound dirs"
```

---

## Task 2: Song⇄Sound override (`is_long` + `/api/sound_type`)

**Files:**
- Modify: `server.py` (add store + `is_long()`; replace two `> LONG_THRESHOLD` sites at ~351 and ~533; add endpoint)
- Create: `tests/test_sound_type.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_sound_type.py`:
```python
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
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_sound_type.py -v`
Expected: FAIL (`module 'server' has no attribute 'is_long'` / `_TYPE_OVERRIDE`).

- [ ] **Step 3: Add the store and helper**

In `server.py`, just after the `LONG_THRESHOLD = 15.0` line (~44) add:
```python
TYPE_OVERRIDE_FILE = os.path.join(DATA_DIR, "type_overrides.json")
```
Then after `duration()` is defined (after ~line 184, anywhere before first use) add:
```python
_TYPE_OVERRIDE = _load(TYPE_OVERRIDE_FILE, {})   # {file: "song"|"sound"}

def is_long(fn):
    """Effective song/sound classification: per-file override beats the 15s rule."""
    ov = _TYPE_OVERRIDE.get(fn)
    if ov == "song":  return True
    if ov == "sound": return False
    return duration(fn) > LONG_THRESHOLD
```
Note: `_load`/`_save` and `DATA_DIR` already exist; `_TYPE_OVERRIDE` must be defined after `_load` (line ~108).

- [ ] **Step 4: Route both classification sites through `is_long`**

At ~line 351, replace:
```python
    dur = duration(fn)
    is_song = dur > LONG_THRESHOLD
```
with:
```python
    dur = duration(fn)
    is_song = is_long(fn)
```
At ~line 533 in the `/api/sounds` row build, replace:
```python
            "long": dur.get(it["file"], 0) > LONG_THRESHOLD,
```
with:
```python
            "long": is_long(it["file"]),
```

- [ ] **Step 5: Add the endpoint**

Add near the other edit endpoints (after `/api/edit`, ~line 975):
```python
@app.route("/api/sound_type", methods=["POST"])
def api_sound_type():
    """Force a clip to song/sound (or auto = 15s rule). Same gate as Add/Edit; shared/global."""
    if not can_edit():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    fn = body.get("file"); typ = body.get("type")
    if fn not in _LIBRARY:
        return jsonify({"ok": False}), 404
    if typ not in ("auto", "song", "sound"):
        return jsonify({"ok": False, "error": "bad type"}), 400
    if typ == "auto":
        _TYPE_OVERRIDE.pop(fn, None)
    else:
        _TYPE_OVERRIDE[fn] = typ
    _save(TYPE_OVERRIDE_FILE, _TYPE_OVERRIDE)
    return jsonify({"ok": True, "type": typ, "long": is_long(fn)})
```

- [ ] **Step 6: Run tests, verify pass**

Run: `./.venv/bin/python -m pytest tests/test_sound_type.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Commit**
```bash
git add server.py tests/test_sound_type.py
git commit -m "feat: per-file song/sound override via is_long() + /api/sound_type"
```

---

## Task 3: Favorites "deck" model (backend)

**Files:**
- Modify: `server.py` (favorites load ~310-318, `user_fav_list`→`user_fav_rec`, the 3 favorites endpoints ~657-690, `toggleFav` server side)
- Create: `tests/test_favorites_deck.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_favorites_deck.py`:
```python
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
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_favorites_deck.py -v`
Expected: FAIL (`_norm_fav` missing; shape mismatches).

- [ ] **Step 3: Add `_norm_fav` and migrate the loader**

Replace the favorites load block (~lines 310-318):
```python
_raw_favs = _load(FAVS_FILE, {})
if isinstance(_raw_favs, list):
    _raw_favs = {"banandon": _raw_favs}
_FAVS_BY_USER = {k: _dedup(v) for k, v in (_raw_favs or {}).items() if isinstance(v, list)}

def fav_key(user):
    return ((user or "").strip().lower()[:40]) or "anon"
def user_fav_list(user):
    return _FAVS_BY_USER.setdefault(fav_key(user), [])
```
with:
```python
def _norm_fav(v):
    """Normalize stored favorites to {'favs':[...], 'deck':[...]} (list = legacy migration)."""
    if isinstance(v, list):
        return {"favs": _dedup([f for f in v if isinstance(f, str)]), "deck": []}
    if isinstance(v, dict):
        favs = _dedup([f for f in v.get("favs", []) if isinstance(f, str)])
        deck = _dedup([f for f in v.get("deck", []) if isinstance(f, str) and f in favs])
        return {"favs": favs, "deck": deck}
    return {"favs": [], "deck": []}

_raw_favs = _load(FAVS_FILE, {})
if isinstance(_raw_favs, list):
    _raw_favs = {"banandon": _raw_favs}
_FAVS_BY_USER = {k: _norm_fav(v) for k, v in (_raw_favs or {}).items()}

def fav_key(user):
    return ((user or "").strip().lower()[:40]) or "anon"
def user_fav_rec(user):
    return _FAVS_BY_USER.setdefault(fav_key(user), {"favs": [], "deck": []})
```

- [ ] **Step 4: Update the three favorites endpoints**

Replace `api_favorites_get` (~657-662):
```python
@app.route("/api/favorites")
def api_favorites_get():
    """Return one user's favorites + deck, filtered to files still in the library."""
    rec = user_fav_rec(request.args.get("user", ""))
    with _LIB_LOCK:
        favs = [f for f in rec["favs"] if f in _LIBRARY]
        deck = [f for f in rec["deck"] if f in _LIBRARY and f in rec["favs"]]
    return jsonify({"favs": favs, "deck": deck})
```
Replace `api_favorite` (~664-676) body to use the rec:
```python
@app.route("/api/favorite", methods=["POST"])
def api_favorite():
    body = request.get_json(silent=True) or {}
    fn = body.get("file")
    if fn not in _LIBRARY:
        return jsonify({"ok": False}), 404
    rec = user_fav_rec(body.get("user"))
    if body.get("on"):
        if fn not in rec["favs"]:
            rec["favs"].append(fn)              # new favorites land in the "rest", not the deck
    else:
        rec["favs"] = [f for f in rec["favs"] if f != fn]
        rec["deck"] = [f for f in rec["deck"] if f != fn]
    save_favs()
    return jsonify({"ok": True, "fav": fn in rec["favs"]})
```
Replace `api_fav_order` (~678-690) to be the deck setter:
```python
@app.route("/api/favorites/order", methods=["POST"])
def api_fav_order():
    """Set the user's deck: an ordered subset of their favorites."""
    body = request.get_json(silent=True) or {}
    rec = user_fav_rec(body.get("user"))
    order = _dedup(body.get("order") or [])
    rec["deck"] = [f for f in order if f in rec["favs"]]
    save_favs()
    return jsonify({"ok": True, "deck": rec["deck"]})
```
(`save_favs()` at line 325 already saves `_FAVS_BY_USER` — no change needed.)

- [ ] **Step 5: Run tests, verify pass**

Run: `./.venv/bin/python -m pytest tests/test_favorites_deck.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Run the whole backend suite**

Run: `./.venv/bin/python -m pytest tests/ -v`
Expected: PASS (all). Then delete the throwaway `tests/test_harness.py`.

- [ ] **Step 7: Commit**
```bash
git rm tests/test_harness.py
git add server.py tests/test_favorites_deck.py
git commit -m "feat: favorites deck model ({favs,deck}) + deck setter endpoint"
```

---

## Task 4: Combined header (frontend)

> Frontend tasks have no JS unit runner; verify by running the app. Start it once for Tasks 4–9:
> `cd ~/Documents/GitHub/SounderServer && SOUND_DIR=~/Downloads/Sounds ./.venv/bin/python server.py` then open `http://127.0.0.1:5000`.
> Authoritative markup/CSS for new components is in `~/dcc-clips/mock/mockup.html`.

**Files:**
- Modify: `templates/index.html` — CSS block (~1-291), header markup (~294-310), `.channels` block (~312-323)

- [ ] **Step 1: Add the header CSS**

In the `<style>` block (before `</style>` at ~290), paste the combined-header rules from the mockup (`build_mock.py` EXTRA → the `header.combo`, `.nowplay`, `.np-grid`, `.np-slot`, `.hdctrls`, `.iconbtn`, `.namecolor.admin` rules). Source of truth: `~/dcc-clips/mock/mockup.html` (search `header.combo`).

- [ ] **Step 2: Replace the two header bars with one**

Replace the `<header>…</header>` (lines ~294-310) and the entire `<div class="channels">…</div>` (lines ~312-323) with a single `<header class="combo">` containing: left `<div class="nowplay">` with `<div id="laneBoxes" class="np-grid">` (populated by the existing lane-render JS — repoint it to fill `.np-grid`), and right `<div class="hdctrls">` holding `#name`, `#nameColor` (add class `namecolor`; admin border toggled in JS), the Sync/Solo icon buttons (`#syncBtn`/`#soloBtn` → icon-only, keep IDs + `title`), the `.vol-ctl`, and a new `⚙` `#adminGear` button. Keep all existing element IDs so the JS keeps working. Use the markup in `~/dcc-clips/mock/mockup.html` (the panel-1 `<header class="combo">`) as the template, swapping mock text for the real IDs.

- [ ] **Step 3: Repoint lane rendering + admin border + gear**

In the lane-render function (the one that filled `#laneBoxes`), render each lane as a `.np-slot` (song lanes get class `song`; idle lanes get `idle` with a "free" label) into `#laneBoxes` (now `.np-grid`). After `ME` is known, toggle the admin border: `$('#nameColor').classList.toggle('admin', ME.admin)`. Move the lane-count steppers + box-volume controls (previously `#songLanesCtl`,`#lanesCtl`,`#boxVolCtl`) into a popover toggled by `#adminGear` (a simple absolutely-positioned `div` shown on click; admin-only). Keep their existing IDs/handlers.

- [ ] **Step 4: Verify in the app**

Reload `http://127.0.0.1:5000`. Confirm: no "Sound Server" label; one header row; now-playing boxes are 2-up (fire two short clips → two half-width boxes; one clip → a single half-width box); right controls on one line; Sync/Solo are icons with tooltips on hover; the color swatch has a green border when logged in as admin; ⚙ opens the lane/box-volume controls.

- [ ] **Step 5: Commit**
```bash
git add templates/index.html
git commit -m "feat: combine header into one row (now-playing grid + single-line controls)"
```

---

## Task 5: Tri-state sort buttons + grouped tabs row

**Files:**
- Modify: `templates/index.html` — CSS (~99-108 + add `.grp`/`.sortbtn`), toolbar/tabs markup (~327-357), sort JS (`SORT`, render ordering ~611-621, `resetFilters` ~850-854)

- [ ] **Step 1: Add the grouping + sort-button CSS**

Paste the `.grp`, `.grp.tabsgrp`, `.sorters`, `.slab`, `.sortbtn`, `.sortbtn .arw`, `.sortbtn.on` rules from the mockup into the `<style>` block.

- [ ] **Step 2: Restructure the tabs row + remove the sort dropdown**

In the tabs row (~327-336), wrap the tab buttons in `<div class="grp tabsgrp">`, add a `<div class="grp sorters" id="sorters">` between the tabs and the right group, and wrap the count+Edit buttons in `<div class="grp rightgrp">`. Delete the `<select id="sortSel">…</select>` (lines ~339-349) from the toolbar.

- [ ] **Step 3: Build the tri-state sort buttons in JS**

Add a sort spec + state and a builder. Replace the `SORT='plays'` default usage: introduce `let SORTFIELD=null, SORTDIR=null;` and map to the existing sort branches.
```javascript
const SORTS = [
  {key:'plays', lab:'🔥 Played', desc:'plays',     asc:'plays_asc'},
  {key:'name',  lab:'A–Z',       desc:'name_desc',  asc:'name'},   // ▲ A→Z
  {key:'added', lab:'🆕 Added',   desc:'newest',     asc:'oldest'},
  {key:'len',   lab:'⏱ Length',  desc:'long',       asc:'short'},
];
function buildSorters(){
  const box=$('#sorters'); box.innerHTML='<span class="slab">Sort</span>';
  for(const s of SORTS){
    const b=document.createElement('button'); b.className='sortbtn';
    b.innerHTML=s.lab+' <span class="arw"></span>';
    b.onclick=()=>cycleSort(s.key); box.appendChild(b); s._el=b;
  }
  paintSorters();
}
function cycleSort(key){
  if(SORTFIELD!==key){ SORTFIELD=key; SORTDIR='desc'; }
  else if(SORTDIR==='desc'){ SORTDIR='asc'; }
  else { SORTFIELD=null; SORTDIR=null; }   // 3rd click clears
  PREV=null;                                // a manual sort cancels the letter-restore memory
  applySort(); paintSorters(); render();
}
function applySort(){
  const s=SORTS.find(x=>x.key===SORTFIELD);
  SORT = s ? s[SORTDIR] : (TAB==='fav' ? 'custom' : 'plays_natural');
}
function paintSorters(){
  for(const s of SORTS){
    const on=s.key===SORTFIELD;
    s._el.classList.toggle('on', on);
    s._el.querySelector('.arw').textContent = on ? (SORTDIR==='desc'?'▼':'▲') : '';
  }
}
```

- [ ] **Step 4: Wire SORT into render() and add the natural orders**

In `render()` ordering (~611-621) keep the existing branches for `plays/plays_asc/name/name_desc/newest/oldest/long/short`. Add: when `SORT==='custom'` use the deck order (Task 7) — for now treat `custom`/`plays_natural` as "no explicit sort" (fall through to the existing `TAB==='fav'` deck/natural branch and the All natural order). In `resetFilters()` (~850-854) replace `SORT='plays'; $('#sortSel').value='plays';` with:
```javascript
  SORTFIELD = null; SORTDIR = null;
  SORT = (TAB === 'fav') ? 'custom' : 'plays_natural';
  if (window.paintSorters) paintSorters();
```
Call `buildSorters()` once at startup (near where filters init).

- [ ] **Step 5: Verify in the app**

Reload. Confirm: the dropdown is gone; four grouped boxes visible (tabs · sort · count/Edit). Click 🔥 Played → ▼ and list sorts most→least; click again → ▲ least→most; third click → arrow gone, natural order returns. Only one sort active at a time.

- [ ] **Step 6: Commit**
```bash
git add templates/index.html
git commit -m "feat: tri-state sort buttons + grouped tabs row (replaces sort dropdown)"
```

---

## Task 6: Filters band — duration slider + NSFW removal

**Files:**
- Modify: `templates/index.html` — CSS (add `.durfilter`, `.toolbar.filters`, `.flab`), toolbar markup (~337-357), filter JS (state, render filter ~605-610, `resetFilters`), remove NSFW (~355, 597, 610, 1036-1038)

- [ ] **Step 1: Add the filters-band + slider CSS**

Paste `.toolbar.filters`, `.flab`, and the `.durfilter*` rules from the mockup into the `<style>` block.

- [ ] **Step 2: Remove NSFW UI + logic**

Delete: the `#nsfwToggle` button (~355), its `style.display` line (~597), the `if(!NSFW_ON) list = list.filter(s=>!s.nsfw)` line (~610), and the `NSFW_ON`/`paintNsfwBtn`/onclick block (~1036-1038). Leave `s.nsfw` in the data untouched.

- [ ] **Step 3: Add the duration slider markup**

Give the toolbar `class="toolbar filters"`, prepend a `<span class="flab">Filter</span>`, and add (after `#typeFilter`) the `.durfilter` block from the mockup with two `<input type=range id="durMin"/durMax">`, a `.fill` bar, and a `.read` span (`id="durRead"`).

- [ ] **Step 4: Add duration filter JS**

```javascript
let DURMIN = 0, DURMAX = Infinity, DURCAP = 60;
function initDurFilter(){
  DURCAP = Math.max(15, Math.ceil(Math.max(0, ...SOUNDS.map(s=>s.dur||0))));
  for(const el of [$('#durMin'), $('#durMax')]){ el.min=0; el.max=DURCAP; }
  $('#durMin').value=0; $('#durMax').value=DURCAP; DURMIN=0; DURMAX=Infinity; paintDur();
}
function paintDur(){
  let lo=+$('#durMin').value, hi=+$('#durMax').value;
  if(lo>hi){ [lo,hi]=[hi,lo]; }
  DURMIN=lo; DURMAX=(hi>=DURCAP)?Infinity:hi;
  $('#durRead').textContent = (hi>=DURCAP) ? (lo+'s+') : (lo+'–'+hi+'s');
  const f=$('#durFill'); if(f){ f.style.left=(lo/DURCAP*100)+'%'; f.style.right=(100-hi/DURCAP*100)+'%'; }
}
$('#durMin').oninput = () => { paintDur(); render(); };
$('#durMax').oninput = () => { paintDur(); render(); };
```
In `render()` filters (~605-610) add:
```javascript
  if(showFilters) list = list.filter(s => (s.dur||0) >= DURMIN && (s.dur||0) <= DURMAX);
```
In `resetFilters()` add: `if($('#durMin')){ $('#durMin').value=0; $('#durMax').value=DURCAP; paintDur(); }`. Call `initDurFilter()` after sounds load (in/just after `loadSounds`).

- [ ] **Step 5: Verify in the app**

Reload. Confirm: NSFW button gone, all clips visible; "Filter" band visually separated; dragging the slider thumbs narrows the grid by length; readout shows `1–32s` and `Ns+` when maxed; switching tabs resets it to full range.

- [ ] **Step 6: Commit**
```bash
git add templates/index.html
git commit -m "feat: duration range filter; remove NSFW gate"
```

---

## Task 7: Favorites deck UI (custom default + deck edit divider)

**Files:**
- Modify: `templates/index.html` — favorites state/load (~449-462, `cacheFavs`/`loadFavs`), render favorites branch (~603, 620), edit-mode render + DnD (~669-714, `reorderFav` ~814-825), `toggleFav` (~864-870), CSS (add `.deckhead`,`.deckdiv`)

- [ ] **Step 1: Add deck state + CSS**

Add `let DECK = [];` near `FAVORDER` (~449). Paste `.deckhead`, `.deckhead.rest`, `.deckdiv` CSS from the mockup.

- [ ] **Step 2: Load favs + deck**

In `loadFavs()` (~453-462) set `FAVSET` from the returned `favs` and `DECK` from the returned `deck`:
```javascript
const d = await (await fetch('/api/favorites?user='+encodeURIComponent(uname()))).json();
FAVSET = new Set(d.favs || []);
DECK = (d.deck || []).filter(f => FAVSET.has(f));
```
Drop the old `FAVORDER` reads/writes (or keep `FAVORDER` as an alias of nothing — simplest: replace all `FAVORDER` uses with `DECK`).

- [ ] **Step 3: Custom ordering = deck then alphabetical**

Replace the favorites natural-order branch (~620):
```javascript
  else if(TAB==='fav'){
    const di=f=>{const i=DECK.indexOf(f);return i<0?1e9:i;};
    list.sort((a,b)=>{
      const da=di(a.file), db=di(b.file);
      if(da!==db) return da-db;                       // deck first, in deck order
      return a.name.localeCompare(b.name);            // rest alphabetical
    });
  }
```
This is reached when `SORT` is `custom`/natural (no explicit sort). Explicit sort buttons still override.

- [ ] **Step 4: Edit mode — render the divider + two zones**

In the favorites edit render path (when `TAB==='fav' && EDITMODE`), split the favorites into `deckItems` (in `DECK` order) and `restItems` (the rest, alphabetical). Render: a `<div class="deckhead">★ Your deck — drag to arrange</div>`, the deck tiles, a `<div class="deckdiv"></div>`, a `<div class="deckhead rest">Other favorites · A → Z</div>`, then the rest tiles. Tag each tile with a `data-zone="deck"|"rest"` attribute so the DnD knows the drop zone.

- [ ] **Step 5: Two-zone DnD updates the deck**

Extend `favDnD`/`reorderFav` (~684-714, 814-825): on drop, compute the new deck from the drop position:
```javascript
function placeInDeck(src, targetFile, targetZone){
  let deck = DECK.filter(f => f !== src);
  if(targetZone === 'deck'){
    const ti = deck.indexOf(targetFile);
    deck.splice(ti < 0 ? deck.length : ti, 0, src);   // insert before target in deck
  } // drop onto the "rest" zone => src removed from deck (already filtered out)
  DECK = deck; cacheFavs();
  render();
  fetch('/api/favorites/order', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({order: DECK, user: uname()})});
}
```
In the drop handler, read the target tile's `data-zone` (or detect a drop below the divider → `rest`) and call `placeInDeck(file, targetFile, zone)` instead of `reorderFav`. Update `cacheFavs()` to persist `DECK` under `ss_favs_<key>`.

- [ ] **Step 6: New favorites land in the rest**

In `toggleFav` (~864-870): on favorite-on, add to `FAVSET` only (do **not** push to `DECK`); on favorite-off, remove from both `FAVSET` and `DECK`. The server already mirrors this.

- [ ] **Step 7: Verify in the app**

Reload. Favorite 3-4 clips. On the Favorites tab with no deck → alphabetical. Click "Edit layout" → divider with empty deck on top, all favorites in the "rest" below. Drag two clips above the divider → they form the deck; "Done" → normal view shows those two first (deck order) then the rest A–Z. Reload the page → deck persists (server round-trip). Drag a deck item back below → it leaves the deck.

- [ ] **Step 8: Commit**
```bash
git add templates/index.html
git commit -m "feat: favorites deck UI (custom default + deck/rest edit divider)"
```

---

## Task 8: Song/Sound toggle in the clip editor

**Files:**
- Modify: `templates/index.html` — editor modal markup (~433-436 area), `openEdit` (~717-737), `editSave` (~781-795), CSS (add `.seg`)

- [ ] **Step 1: Add the segmented-control CSS + markup**

Paste the `.seg` CSS from the mockup. In the editor modal, before the Volume label (~433), add:
```html
<label>Type</label>
<div class="seg" id="typeSeg">
  <button data-ty="auto">Auto</button>
  <button data-ty="song">🎵 Song</button>
  <button data-ty="sound">Sound</button>
</div>
```

- [ ] **Step 2: Reflect current type on open**

In `openEdit(s)` (~717), set the active segment from the clip's current effective type. The clip's `s.long` tells the effective song/sound; for the explicit override we also want to show Auto vs forced. Add a per-clip override field to `/api/sounds` is overkill — instead show: highlight `song` if `s.long` else `sound`, and treat the third state via the button the user clicks. Set: `setTypeSeg(s.long ? 'song' : 'sound')`. Add:
```javascript
let EDIT_TYPE = 'auto';
function setTypeSeg(ty){ EDIT_TYPE=ty;
  document.querySelectorAll('#typeSeg button').forEach(b=>b.classList.toggle('on', b.dataset.ty===ty)); }
document.querySelectorAll('#typeSeg button').forEach(b=> b.onclick=()=>setTypeSeg(b.dataset.ty));
```

- [ ] **Step 3: POST on save**

In `editSave` (~781-795), after the existing trim/rename calls succeed, add:
```javascript
  await fetch('/api/sound_type', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({file: EDITCUR.file, type: EDIT_TYPE})});
```
Then the existing `await loadSounds()` refreshes `s.long`.

- [ ] **Step 4: Verify in the app**

Reload, log in as editor. Open a short clip's editor → "Sound" highlighted. Set "🎵 Song", Save. Confirm the clip now appears under the Songs filter and routes to a song lane when fired. Re-open → set "Auto", Save → reverts to its duration-based type.

- [ ] **Step 5: Commit**
```bash
git add templates/index.html
git commit -m "feat: Auto/Song/Sound toggle in the clip editor"
```

---

## Task 9: Letter filter forces A→Z, restores on All

**Files:**
- Modify: `templates/index.html` — alpha bar handlers (~835-838), add `PREV_SORT`

- [ ] **Step 1: Add restore memory**

Add `let PREV = null;` near the sort state (a `{field, dir}` snapshot, or null). In the letter button click (~838) and the "All" button (~835):
```javascript
// letter button:
b.onclick = () => { if(b.disabled) return;
  if(ALLLETTER===null && SORTFIELD!=='name'){ PREV = {field:SORTFIELD, dir:SORTDIR}; }
  ALLLETTER=L; $('#search').value='';
  SORTFIELD='name'; SORTDIR='asc'; applySort(); paintSorters();   // force A→Z
  markAlpha(); render();
};
// "All" button:
all.onclick = () => {
  ALLLETTER=null;
  if(PREV){ SORTFIELD=PREV.field; SORTDIR=PREV.dir; PREV=null; applySort(); paintSorters(); }
  markAlpha(); render();
};
```
Declare `let PREV = null;` with the sort state. (`applySort`/`paintSorters` from Task 5; `'name'+'asc'` = A→Z per the SORTS map.)

- [ ] **Step 2: Verify in the app**

Reload. Sort by 🔥 Played ▼. Click a letter → list filters and sort flips to A→Z (Played arrow clears, A–Z shows ▲). Click "All" → returns to 🔥 Played ▼. On Favorites: letter → A→Z, "All" → back to Custom deck order.

- [ ] **Step 3: Commit**
```bash
git add templates/index.html
git commit -m "feat: letter filter forces A→Z and restores prior sort on All"
```

---

## Task 10: Full verification pass

**Files:** none (manual)

- [ ] **Step 1: Backend suite green**

Run: `./.venv/bin/python -m pytest tests/ -v`
Expected: PASS (all of Tasks 2-3).

- [ ] **Step 2: End-to-end manual checklist (run the app)**

Run `SOUND_DIR=~/Downloads/Sounds ./.venv/bin/python server.py`, open `http://127.0.0.1:5000`, and confirm each spec item: combined one-row header; now-playing 2-up (lone box = half); single-line controls never wrap; admin-bordered swatch; icon Sync/Solo tooltips; ⚙ admin popover; grouped tabs/sort/right boxes; tri-state sort cycle (off→▼→▲→off, single-active); filters band with duration slider; NSFW gone (flagged clips visible); Favorites default = deck-then-alpha (alpha when empty); deck edit divider drag add/remove persists across reload; Song/Sound editor toggle re-routes lanes + Songs/Sounds filter; letter→A→Z then All restores prior sort.

- [ ] **Step 3: Update CLAUDE/memory note (optional)**

No code change. If anything diverged from the spec, note it in the spec doc.

---

## Notes for the implementer

- `data/type_overrides.json` and `data/favorites.json` are runtime state (gitignored, not deployed). The favorites migration is non-destructive: an old flat list loads as `{favs:list, deck:[]}`.
- Keep every existing element ID when restructuring the header/toolbar — the JS references them by ID.
- The mockup `~/dcc-clips/mock/mockup.html` is the visual source of truth; copy its CSS/markup rather than re-inventing.
- Deploy (later, when asked): rsync `templates/` + `server.py` to the VPS and restart (`pkill -f server.py`; systemd respawns). `data/` is excluded, so prod favorites migrate in place on first load.
