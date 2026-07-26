# Family Recipes → Store List

Design doc — 2026-07-26

A phone-first recipe collection for the household, and a shopping checklist
generated from it, ordered to match how we actually walk our Shaws.

Lives inside SounderServer as a Flask blueprint at `/recipes`.

## Why

Four people in the house cook and shop. Recipes live in scattered bookmarks and
memory. Shopping lists get texted around and lost. The trip itself is
inefficient because the list isn't in store order, so you backtrack.

The fix is one shared list that several phones write to, built out of recipes we
keep, sorted into the five stretches of the store in the order we walk them.

## Constraints

- **Phone first.** Desktop is incidental. Every target ≥44 px; the store list
  must be usable one-handed, in a coat, pushing a cart.
- **Household is vegetarian.** No meat or seafood section exists. Dairy and eggs
  are in; plant-based protein files under Middle Aisles.
- **Multiple concurrent writers.** Two people shopping in different aisles must
  see each other's check-offs.
- **Sometimes online, sometimes in-store.** Some trips are Shaws pickup orders
  placed on their website; some are walking the store with the phone.
- **Low maintenance wins over completeness.** Any feature that requires ongoing
  bookkeeping will rot and start lying to us.

## Decisions

Each of these was chosen against alternatives; the rejected ones are recorded so
we don't relitigate them.

### One shared list, not per-person

A single live list on the server. Anyone opens the site and sees the same state.
Recipes add to it rather than replacing it.

*Rejected:* per-person lists (defeats the point — we shop for one household);
stateless session lists (loses the "we're out of milk" note from the couch).

### Hybrid ingredients: freeform line + optional pantry link

A recipe ingredient stores the human line (`2 cloves garlic, minced`) so the
recipe reads correctly when cooking, **plus** parsed `qty`/`unit`, **plus** a
nullable pointer to a `pantry_item`.

The pantry item is the canonical grocery thing. It owns the store section, the
staple flag, the purchase unit, and the Shaws product link. Filing an ingredient
once teaches the system forever.

Unmatched ingredients are not an error — they land in **Unsorted** at the bottom
of the store list with a one-tap "file this," which creates or links a pantry
item and remembers. The Unsorted bucket drains naturally over a few months.

*Rejected:* pure freeform (`garlic` / `3 cloves garlic` / `garlic cloves` never
merge, aisles never stick); mandatory pantry matching up front (turns adding a
recipe into data entry, so nobody adds recipes).

### Purchase unit comes from the Shaws product

Recipes speak in recipe units. The pantry item speaks in what goes in the cart.
If `Milk` points at "Shaws Whole Milk, 1 gal", the list line reads
**Milk — need 3½ cups → 1 gal**, not a request for pints. No separate
"buy unit" concept is needed; the chosen product *is* the buy unit.

### "Already have it" is transient, plus a staples flag — never inventory

Adding a recipe opens a sheet with every ingredient pre-checked. Uncheck what's
already in the fridge. The site remembers nothing about that decision.

Separately, pantry items can be flagged **staple** (salt, olive oil, flour).
Staples arrive pre-*un*checked and collapsed to one line —
`▸ 5 staples skipped — cumin, chili powder, garlic, olive oil, salt`. Tap to
expand when you're actually out.

*Rejected:* standing inventory tracking. With two young adults in the house,
things come and go unrecorded; the model would be wrong within a week and then
actively misleading.

### Store sections: five fat groups, hidden sub-order

Five headers, walked in this order, plus Unsorted. The sub-categories are
**sort keys with no UI presence** — they exist so items land in the right part of
a section without adding headers to scroll past. Within a sub-category, sort is
alphabetical.

| # | Section | Sub-categories, in order |
|---|---------|--------------------------|
| 1 | Produce & Fancy Cheese | produce · fancy cheese |
| 2 | Early Aisles | shelf-stable fruit · coffee & tea · cereal · breakfast (granola, bars) · spices · baking |
| 3 | Middle Aisles | impossible/plant meat · broths · soups · box dinners · pasta · pasta sauce · canned veg · rice · asian · mexican · chips · cookies · salty snacks |
| 4 | Late Aisles | candy · canned teas · paper · home (batteries, trash bags, detergent) · medicine · toiletries · soda · spindrift & drinks · seltzer · wine · beer · liquor |
| 5 | Freezer / Dairy / Bread | freezer · dairy · bread |
| — | Unsorted | *(always last; new items land here)* |

Sort key is `(section.position, subsection.position, name)`. Sections and
sub-categories are both reorderable in the app — this table is a seed, not a
constant.

Note: "fancy cheese" (section 1) and everyday shredded/sliced cheese (section 5)
are different pantry items filed in different places. That is correct, not a bug.

### Check-off behavior: sink into a fold

Tapping an item removes it from view and drops it into a `✓ n in the cart` fold
under its section. The screen only ever shows what's still needed. Tap the fold
to review or undo.

*Rejected:* strike-through in place (screen fills with things you already have);
collapsing the whole section on completion (jumpy when a second shopper checks
off the last item in a section you're standing in).

### Trip lifecycle: additive list + meal-plan strip

The list is permanent and additive, like a notepad on the fridge. A **Finish
trip** button clears everything checked in one action; unchecked items (couldn't
find it) survive to the next trip.

The list page carries a strip showing which recipes currently feed it —
`This week: Chili · Tacos · Mac & cheese`. Tap through to a recipe while
cooking, or pull a recipe off, which withdraws its contributions without
disturbing what another recipe asked for. **Finish trip** clears checked items
but keeps the meal strip, since those meals are about to be cooked.

*Rejected:* explicit saved trips with history. Nobody will open March.

### Browse layout: dense rows

Recipe rows with a thumbnail, name, `45 min · serves 6 · smitten kitchen`, and a
⊕ to queue it. About seven per screen at 390 px.

*Rejected:* two-across photo cards. Only marginally less dense in practice, but
they make every recipe owe you a photo — a tax on the one action that must stay
frictionless. Rows degrade gracefully to an emoji or letter tile.

Multi-select has no mode: tap ⊕ on several recipes, a badge counts them, then
the add sheet runs once per recipe.

## Architecture

A Flask **blueprint** in the existing app, not a second service.

```
recipes/
  __init__.py     blueprint factory
  db.py           schema, migrations, connection helper
  api.py          routes
  parse.py        ingredient line → qty / unit / name / prep
  units.py        conversion families + merge
  seed.py         default sections and sub-categories
templates/
  recipes.html    single page, three tabs
data/
  recipes.db      SQLite
```

`server.py` changes by exactly two lines — an import and
`app.register_blueprint(recipes_bp, url_prefix="/recipes")`. Recipe code never
opens `server.py`; soundboard code never opens `recipes/`.

Deploy is unchanged: rsync picks up the new directory, `systemctl restart
soundserver`. No new systemd unit, no Caddy change, no second login.

*Rejected:* folding into `server.py` / `index.html` — they are already 1068 and
1819 lines, and a soundboard shares no concepts with a grocery list. *Rejected:*
a separate app on `recipes.sounderserver.party` — two services to keep alive, two
deploys, a second session cookie, and it stops feeling like one site.

### Storage: SQLite

`data/recipes.db`, same backup story as the existing `catalog.db`. The data is
relational and the write pattern is many small concurrent updates (four people
tapping checkboxes), which is exactly what JSON files handle badly.

### Schema

- **`section`** `(id, name, position)`
- **`subsection`** `(id, section_id, name, position)`
- **`pantry_item`** `(id, name, subsection_id, is_staple, buy_unit, shaws_url,
  shaws_sku, notes)`
- **`pantry_alias`** `(pantry_item_id, alias)` — "scallions" → green onions, so
  two recipes' wording merges to one line
- **`recipe`** `(id, name, source_name, source_url, servings, time_minutes,
  instructions, notes, photo_url, created_by, created_at, archived)`
- **`recipe_ingredient`** `(id, recipe_id, position, raw_text, qty, unit,
  pantry_item_id NULL)`
- **`list_line`** `(id, pantry_item_id NULL, free_text, checked, checked_by,
  checked_at, created_at)` — one row per line seen at the store
- **`list_contribution`** `(id, list_line_id, recipe_id NULL, added_by, qty,
  unit, raw_text)` — each claim on that line
- **`meal_plan`** `(recipe_id, added_by, added_at)`

The `list_line` / `list_contribution` split is what makes both merging and
un-adding work. Chili wants 1 onion and the stir fry wants 2: one line showing
`3`, two contributions. Remove chili and the line drops to `2` rather than
vanishing.

### Ingredient parsing

`parse.py` is the one genuinely fiddly component, so it is isolated and directly
unit-tested. It must handle fractions (`1½`, `1 1/2`), ranges (`2-3`), unicode
vulgar fractions, parentheticals (`(about 14 oz)`), and prep clauses after the
comma.

It is allowed to fail. An unparseable line still becomes a list item — just an
unmerged, unfiled one. Parsing quality affects convenience, never correctness.

### Unit merging

`units.py` defines conversion families: volume (tsp, tbsp, cup, pint, quart,
gallon, ml, l), weight (oz, lb, g, kg), count (each, clove, bunch, can, pkg).
Quantities sum within a family and are rendered in the most readable unit.
Across families or with unknown units they stack — `3½ cups + 1 splash` — rather
than guessing.

### Sync

Polling, matching the soundboard's existing pattern. A version counter bumps on
every write; the List tab polls `?since=<v>` every few seconds and receives only
changed rows. No websockets, no new dependency.

### Auth

Reads open, writes gated by the existing `can_edit()` / `USER_PASS`. Consistent
with the soundboard, where listening is open and editing is not. Tightening
reads later is one decorator.

Identity reuses the soundboard's display name from `localStorage.ss_name`, with
colors already persisted per name in `user_colors.json`. If you've used the
soundboard, recipes already knows who you are. There are no accounts; it is an
honor-system name, which is right for a household.

## Screens

Three tabs: **Recipes · List · Pantry**.

**Recipes** — search, filter chips, dense rows, ⊕ to queue. Tap the name to open
the recipe.

**Recipe detail** — ingredients as written, instructions, source link, edit,
*Add to list*.

**Add sheet** — every ingredient pre-checked, staples folded to one line,
`Add n items to list`.

**List** — meal-plan strip, five section headers, items sinking into per-section
folds as they're checked, a free-text add field ("trash bags"), and *Finish
trip*.

**Pantry** — rarely opened, but it needs a home: staple flags, section and
sub-category assignment, Shaws product links, and section reordering.

## Shaws integration

The site never drives a browser. `pantry_item` accumulates product URLs as we
pick things we like, and an export endpoint returns the unchecked list as JSON
with those URLs attached. Filling the cart is an out-of-band Claude session
reading that export and driving Claude-in-Chrome.

This is why the product link belongs on the pantry item rather than the recipe:
choosing the good cheddar once means every recipe calling for cheddar knows
about it.

## Recipe import

An endpoint accepting structured recipe JSON, so a link handed to Claude can be
fetched, parsed, and POSTed. Plus a plain add/edit form for recipes that come
from a person rather than a URL.

## Testing

`pytest`, in the existing `tests/`. `parse.py` and `units.py` get direct
coverage since they hold the real complexity. The API gets lifecycle tests: add
recipe → add to list → merge with a second recipe → check off → finish trip →
verify unchecked items survived and the meal strip persisted.

## Out of scope

Nutrition, scaling servings, meal-plan calendars, per-person accounts, saved
trip history, inventory tracking, price tracking, and any automated interaction
with Shaws from the server.
