# La-Mulana 2 tracker

Lives at `/lamulana`. Blueprint in `lamulana/`, own SQLite database at
`data/lamulana.db`, frontend in `templates/lamulana.html`. Reads are open;
writes need the same login session the soundboard and recipes use
(`can_edit()` / `need_edit()`, now factored into a shared repo-root `auth.py`
that the soundboard and both blueprints import — copy that file too if you're
setting this up somewhere new).

The database creates its schema and seeds itself on first import — nothing to
run by hand, no migration step, no seed script to invoke separately.

## Deploying it

Production `server.py` on the box has previously held code that exists in no
commit anywhere, so it is edited in place by hand, never overwritten or
rsynced wholesale:

1. Copy `lamulana/`, `auth.py`, and `templates/lamulana.html` to the box.
2. Edit the production `server.py` by hand, adding this block after the
   existing `app.register_blueprint(recipes_bp)`:

   ```python
   # The tracker owns its own database and bootstraps it at import. Mount it
   # defensively: an unwritable or corrupt lamulana.db must cost us
   # /lamulana, not the soundboard and the recipes list in the same process.
   try:
       from lamulana import lamulana_bp
       app.register_blueprint(lamulana_bp)
   except Exception:
       traceback.print_exc()
   ```

   Needs `import traceback` near the top of `server.py` if it isn't already
   there — check before pasting, since the production file has drifted from
   any single commit before.

   It's wrapped in `try`/`except` on purpose — a mount failure (an unwritable
   `data/` directory, a corrupt `lamulana.db`, whatever) prints a traceback to
   the service log and drops `/lamulana`, but the soundboard and `/recipes`
   keep serving in the same process. If `/lamulana` 404s after a deploy,
   check the log before assuming the whole app is down.
3. Restart: SSH in as `root@<VPS_IP>` (not `sudo` from the `sound` user —
   `systemctl restart` needs root, and the service itself runs as `sound`
   per `deploy/soundserver.service`), then `systemctl restart soundserver`.
   See `deploy/DEPLOY.md` for the full house convention.

## Adding to the seed

`lamulana/seed.py` holds `AREAS` and `CHECKLIST` — pure data, no imports.
Re-running the seed after editing it is safe: `seed_areas()` and
`seed_checklist()` (in `lamulana/db.py`, run automatically at import) insert
new rows and update `position` on existing ones, and never touch a `done`
flag, `done_at`, or a `note` you've already written. Renaming or deleting a
row in `seed.py` doesn't prune the old database row either — it's left
behind rather than risk silently detaching progress or orphaning a clue/thread
pointing at a removed area.

## The backend, if you're touching it again

`lamulana/api.py` is the whole HTTP surface — clues, threads, the link
between them, search, and the checklist — and it's grown to ~750 lines doing
it. There's a recorded, deliberately deferred plan to split the checklist
routes into `lamulana/checklist.py` with a shared `lamulana/common.py`
underneath both files; the naive version of that split (just move the
checklist routes) doesn't work because of a helper (`_checklist_groups()`)
that both `api_bootstrap` and the checklist routes need. See the File
Structure section of
`docs/superpowers/plans/2026-08-16-lamulana-tracker.md` for the full
reasoning before attempting it.

The design is documented separately in
`docs/superpowers/specs/2026-08-16-lamulana-tracker-design.md` — this file is
just the deploy/operate note.
