"""Concurrent reads must not corrupt each other.

The store-list screen polls `/api/list` every four seconds from every phone in
the house, and the whole point of the feature is several people shopping at
once. Under waitress (16 threads) that is genuinely parallel traffic against
one Flask app.

The failure this guards against: a single shared `sqlite3.Connection` keeps a
prepared-statement cache keyed by SQL text. Two threads running the *same*
query at the same time get handed the *same* underlying statement, and one
thread resetting it mid-fetch leaves the other holding rows whose tuple is
shorter than the column description it was built with. That surfaces as
`IndexError: tuple index out of range` from `dict(row)` — a 500 to a phone
standing in an aisle.

These tests hammer the read endpoints hard enough to hit that window. They
fail against a shared module-level connection and pass with per-thread ones.
"""

import importlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

# Enough concurrency to overlap inside a single query, and enough repeats that
# a narrow window still gets hit. Kept small enough to stay a fast unit test.
THREADS = 16
REQUESTS = 320


def _seed(client):
    """Enough rows that each read does real work and holds a statement open."""
    for name in ("Onions", "Tomatoes", "Milk", "Butter", "Rice",
                 "Chicken thighs", "Olive oil", "Parmesan"):
        client.post("/recipes/api/pantry", json={"name": name, "who": "seed"})
    client.post("/recipes/api/recipes", json={
        "name": "Weeknight pasta", "who": "seed",
        "ingredients": ["2 onions, diced", "1 lb tomatoes", "3 tbsp olive oil"],
    })
    for name in ("Milk", "Butter", "Rice"):
        client.post("/recipes/api/list/add", json={"name": name, "who": "seed"})


def _hammer(app, path):
    """Fire REQUESTS at `path` from THREADS threads. Returns the failures."""
    def one(_):
        # A client per call: werkzeug's test client keeps a cookie jar, and
        # sharing one across threads would be its own race, unrelated to the
        # connection bug under test.
        try:
            resp = app.app.test_client().get(path)
            if resp.status_code != 200:
                return f"{resp.status_code}"
            resp.get_json()          # force the body to actually deserialize
            return None
        except Exception as exc:     # noqa: BLE001 - any crash is a failure here
            return f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        return [f for f in pool.map(one, range(REQUESTS)) if f]


@pytest.mark.parametrize("path", ["/recipes/api/list",
                                  "/recipes/api/sections",
                                  "/recipes/api/pantry"])
def test_concurrent_reads_all_succeed(app, recipes_client, path):
    _seed(recipes_client)
    failures = _hammer(app, path)
    assert not failures, (
        f"{len(failures)}/{REQUESTS} concurrent reads of {path} failed: "
        f"{failures[:5]}"
    )


def test_concurrent_reads_across_endpoints_all_succeed(app, recipes_client):
    """Mixed traffic — different queries interleaving on the same connection."""
    _seed(recipes_client)
    paths = ["/recipes/api/list", "/recipes/api/sections",
             "/recipes/api/pantry", "/recipes/api/recipes",
             "/recipes/api/list/poll?since=-1"]

    def one(i):
        path = paths[i % len(paths)]
        try:
            resp = app.app.test_client().get(path)
            if resp.status_code != 200:
                return f"{path} -> {resp.status_code}"
            resp.get_json()
            return None
        except Exception as exc:     # noqa: BLE001
            return f"{path} -> {type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        failures = [f for f in pool.map(one, range(REQUESTS)) if f]
    assert not failures, (
        f"{len(failures)}/{REQUESTS} mixed concurrent reads failed: "
        f"{failures[:5]}"
    )


def test_writes_under_concurrent_reads_never_hang(app, recipes_client):
    """The realistic shape: one person adding while everyone else polls.

    `_db.LOCK` is a plain `threading.Lock`, which is not reentrant — a write
    path that acquired it twice would wedge that request thread forever. On a
    phone in a store that reads as the app freezing, with no error to see. The
    timeout below is the assertion: a deadlock shows up as an unfinished
    future, not as a wrong answer.
    """
    _seed(recipes_client)

    def writer(i):
        c = app.app.test_client()
        with c.session_transaction() as s:
            s["can_edit"] = True
        resp = c.post("/recipes/api/list/add",
                      json={"name": f"Thing {i % 7}", "who": "writer"})
        return None if resp.status_code == 200 else f"write -> {resp.status_code}"

    def reader(i):
        resp = app.app.test_client().get("/recipes/api/list")
        return None if resp.status_code == 200 else f"read -> {resp.status_code}"

    def one(i):
        try:
            return (writer if i % 4 == 0 else reader)(i)
        except Exception as exc:     # noqa: BLE001
            return f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        futures = [pool.submit(one, i) for i in range(120)]
        # 30s is enormous for 120 household-scale requests; only a deadlock or
        # a lock convoy gets anywhere near it.
        results = [f.result(timeout=30) for f in futures]

    failures = [r for r in results if r]
    assert not failures, (
        f"{len(failures)}/120 mixed read+write calls failed: {failures[:5]}"
    )


def test_each_thread_gets_its_own_connection(app):
    """The structural half of the fix, asserted directly.

    Without this, a future refactor could make the reads pass by accident
    (timing, a smaller statement cache) while the sharing quietly returned.
    """
    api = importlib.import_module("recipes.api")
    barrier = threading.Barrier(8)

    def grab(_):
        # All eight in flight at once, so none can be handed a connection a
        # finished thread already released back to a pool.
        barrier.wait(timeout=10)
        return id(api._conn())

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(grab, range(8)))
    assert len(set(ids)) == 8, (
        f"8 concurrent threads shared {8 - len(set(ids)) + 1} connection(s)"
    )

    # ...and a single thread must reuse its own rather than reconnecting.
    with ThreadPoolExecutor(max_workers=1) as pool:
        repeated = list(pool.map(lambda _: id(api._conn()), range(4)))
    assert len(set(repeated)) == 1, "one thread should reuse one connection"
