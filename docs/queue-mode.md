# Queue mode (2026-10-10)

Brandon wants songs to be able to line up instead of cutting each other off, plus seek, skip,
and "start EPIC at a saga / track and play to the end".

## Model

One shared **song queue** lives on the server (`data/queue.json` persists the mode and the
upcoming items; the currently-playing item lives in `_ACTIVE` like any other sound).

- **Mode** is global: `overlap` (today's behaviour) or `queue`. It only changes what a plain
  click on a **song** does. In queue mode a song click appends to the queue. Short sounds always
  fire immediately in their lanes, in both modes.
- The queue **always plays** while it has items, whatever the mode: an advancer (1 s thread, also
  run on every `/api/active`) starts the next item in lane `song0` once no song is active. A song
  fired by hand in overlap mode interrupts the queue's current song; the queue simply continues
  after it.
- The current item is a normal `_ACTIVE` entry flagged `"queue": true`, so browsers and room
  nodes play it with no new client logic. Natural end = the existing prune (start + dur + pad).
- **Skip** removes the current entry, so the advancer moves on. **Prev** puts the current item back
  at the front and replays the previous one (a 20-item history).
- **Seek** rewrites the entry's `start` (`start = now - pos - SYNC_BUFFER`) and bumps a `seek`
  counter. Browsers see the new start on their next poll and jump there. Nodes compare the start
  they began from and restart the stream at the new offset.
- **Targets (optional per item)**: `nodes: ["kitchen"]` + `boxes_only` lets the house play on one
  room only through the server queue. Nodes not listed ignore that entry. This is the path for the
  house presets to move onto later; the node's file-based local queue stays as is meanwhile.

## Ordered tags and sections

`GET /api/tag_order/<slug>` returns the tag's songs sorted by the first number in the filename
(`e1_…`, `e17_…`, the `epic_NN_` stragglers too), plus named sections. Sections come from
`data/tag_order.json`, with a built-in default for `e` (EPIC: The Musical): Troy 1, Cyclops 6,
Ocean 10, Circe 14, Underworld 18, Thunder 21, Wisdom 26, Vengeance 31, Ithaca 36.

`POST /api/queue/from {tag, file | section | index, mode: replace|append}` queues from that point
to the end of the tag.

## API

| Route | Who | Does |
|---|---|---|
| `GET /api/queue` | anyone | state: mode, current (token, start, dur, pos), items, history count |
| `POST /api/queue/add {files:[…], mode}` | anyone (`replace` needs editor or house token) | enqueue |
| `POST /api/queue/from {tag, file/section/index, mode}` | same | play-from-here |
| `POST /api/queue/skip`, `/prev` | anyone | next / previous |
| `POST /api/queue/seek {token, pos}` | anyone | jump within a playing song |
| `POST /api/queue/move {id, to}`, `/remove {id}` | anyone | reorder / drop |
| `POST /api/queue/clear`, `/stop`, `/mode {mode}` | editor, admin, or `X-House-Token` | |

`X-House-Token` = `data/house_token` (created on first start, chmod 600). `/api/active` also
carries a compact `queue` object so the board repaints without an extra poll.

## UI

- Header: an **Overlap | Queue** switch next to Now playing, plus a 🎶 queue panel (count badge)
  with the upcoming list (↑ ↓ ✕), Clear, ⏮ ⏭.
- The playing song's slot gets a **seek slider**.
- In a tag view: **➕ Queue all**, and **Start at…** (a track picker), plus saga chips for tags
  with sections.
