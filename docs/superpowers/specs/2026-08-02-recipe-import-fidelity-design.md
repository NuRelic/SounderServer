# Recipe import fidelity — design

**Date:** 2026-08-02
**Status:** approved, ready to plan

## The problem

The stored Paneer Tikka Masala (recipe 3) is missing a quarter of its source
recipe. The source page publishes a complete schema.org `Recipe` JSON-LD block;
it was never read. The recipe was written from a prose skim instead.

Measured against
`https://www.indianhealthyrecipes.com/paneer-tikka-masala-recipe-sanjeev-kapoor/`:

| | Source | Stored |
|---|---|---|
| Ingredients | 26 | 19 |
| Instruction steps | 15, in 3 named sections | 5 paraphrased paragraphs |
| Cooking methods | 3 (oven / griddle / air fryer) | 1 |

Dropped entirely: salt (twice — marinade and gravy), water (¾ + ¾ cup), the
whole spices (cardamom, cinnamon, cloves, bay), ½ bell pepper, ½ small onion,
and the gravy's 1½ tsp ginger garlic paste.

Four further defects of the same kind:

1. **Silent rescaling.** Quantities were doubled (250 g paneer → 500 g) but
   servings went 4 → 6. Doubling 4 is 8. The stored recipe is internally
   inconsistent and nothing noticed.
2. **Ranges collapsed to a precision the source never claimed.** "¼ to ½ tsp
   Kashmiri chili" became a flat number. `parse.py:38` discards the high end of
   every range by design.
3. **Invented nutrition.** The stored notes claim "~22g protein, ~7g fiber."
   The source's own nutrition block says 13 g protein, 2 g fiber per serving.
   Those numbers were not computed from anything.
4. **Unresolved quantity references.** A stored step reads "Return to the pan
   with the remaining oil." Oil appears on three separate lines. At the stove
   this is unusable.

## Goals

1. Nothing from a source recipe is dropped without that being visible.
2. Every instruction step carries the actual amount to use, not "the rest."
3. Ingredients separate into components — what the recipe needs period vs. the
   marinade vs. the gravy vs. the protein — with both a grouped view for
   cooking and a flat, summed view for shopping.

## Non-goals

- A UI "import from URL" button for other family members. The endpoint makes
  one easy later; nobody has asked for it.
- Per-step ingredient highlighting in the UI. Quantity resolution is stored as
  resolved step text, not as a persisted step↔ingredient link table.
- Nutrition calculation. We store what the source published; we do not compute.

## Root cause

Two independent failures compounded.

**Extraction.** A prose skim was used where structured data was available. This
is a procedural failure and could recur on any import.

**Representation.** Even done perfectly, the current schema could not have held
the result. `POST /api/recipes` takes `ingredients` as a flat list of strings —
there is nowhere to record a component group. `instructions` is a single TEXT
blob — there is nowhere to record a step, let alone a resolved one.

Fixing only the procedure leaves goals 2 and 3 impossible. Fixing only the
schema leaves nothing checking the import. Both are needed.

## Approach

Split the work by what is mechanical and what is judgment, and put a
machine-checkable gate between them.

- **Mechanical, in code:** fetch the URL, extract the source's ingredient lines
  and steps verbatim from JSON-LD. This step cannot omit anything.
- **Schema:** hold component groups, optional flags, ranges, and steps with
  both source and resolved text.
- **Judgment, at import time:** assign components, resolve quantity
  references, decide substitutions and exclusions.
- **Gate:** every source ingredient line must be accounted for, or the save is
  rejected.

The tikka failed because nothing checked. The gate is the load-bearing piece.

## Data model

`recipes/db.py` auto-syncs any new nullable or defaulted column and creates any
brand-new table on boot (`_sync_columns`, `migrate`). Everything below lands
with no hand-written `MIGRATIONS` step.

### `recipe_ingredient` — six new columns

| Column | Type | Purpose |
|---|---|---|
| `component` | TEXT | `"Marinade"`, `"Gravy"`, `"Garnish"`. NULL = base, needed for the recipe period |
| `component_pos` | INTEGER | group display order; `position` breaks ties within a group |
| `optional` | INTEGER DEFAULT 0 | the source marked it optional |
| `qty_high` | REAL | the top of a range; `qty` holds the low end |
| `excluded` | INTEGER DEFAULT 0 | deliberately not used in our version |
| `excluded_reason` | TEXT | why — e.g. `"household is vegetarian"` |

Plus `source_text TEXT`: the source's line verbatim, kept distinct from
`raw_text`, which is ours and may be scaled or substituted. The gate compares
against `source_text`; without the separation there is nothing to compare to.

`parse.py` changes to keep the high end of a range in `qty_high` rather than
discarding it.

### `recipe_step` — new table

```sql
CREATE TABLE IF NOT EXISTS recipe_step (
    id          INTEGER PRIMARY KEY,
    recipe_id   INTEGER NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    section     TEXT,            -- "Preparation", "Make the gravy"
    text        TEXT NOT NULL,   -- resolved: "Heat 1 tbsp oil"
    source_text TEXT             -- verbatim: "Heat half of the oil"
);
```

Both texts are always stored. This follows the rule `db.py` already states for
`recipe_ingredient.prep`: parse once at save time, never re-derive on read, or
a later code change silently rewrites recipes nobody edited. A resolved
quantity is a judgment recorded at import; `source_text` is the receipt.

`recipe.instructions` stays. Steps render when present, the blob otherwise, so
the two older recipes are untouched.

### `recipe` — three new columns

| Column | Type | Purpose |
|---|---|---|
| `source_servings` | INTEGER | what the source said (4) |
| `scale` | REAL | what we multiplied by (2.0) |
| `source_nutrition` | TEXT | the source's per-serving nutrition, as JSON |

The first two make the 4 → 6 inconsistency arithmetically checkable. The third
gives the real numbers a home so they stop being invented.

## Import pipeline

### `recipes/importer.py`

```
fetch(url)    -> html          # thin, network
extract(html) -> SourceRecipe  # pure, no network, all the logic
```

`extract` walks nested `@graph` structures — the recipe is rarely top-level on
a WordPress food site — and returns ingredient lines and steps verbatim, with
`HowToSection` names preserved as step sections.

Keeping `extract` pure means it tests against saved HTML fixtures with no
network. This matters: these pages are ~1 MB of ad markup and change weekly.

Fallback order: JSON-LD → microdata → **fail loudly**. There is deliberately no
degradation path to reading prose, because that is the failure being fixed. A
JS-rendered or paywalled page fails at `fetch`, and Chrome automation supplies
the markup; extraction still runs on real structured data.

### `POST /api/recipes/import {url}`

Returns a draft. Saves nothing. The judgment pass happens on the draft, and the
finished recipe goes through the normal create endpoint — so the gate is on the
only path that writes.

## The gate

`POST /api/recipes` accepts an optional `source_ingredients: [...]`. When
present:

- every source line must be claimed by exactly one stored row via `source_text`
- any unclaimed line → **422**, with the unclaimed lines named in the response
- a row may claim its line and set `excluded: true` with a reason — still
  stored, just not cooked or shopped
- rows with no `source_text` are ours to add freely

Rejection, not a warning. Under this gate the tikka import fails with *"7
source ingredients unaccounted for: salt, salt, water, green cardamoms, bell
pepper, onion, ginger garlic paste."*

Two further checks on the same path:

- **Servings arithmetic.** When `source_servings` and `scale` are both set,
  `servings` must equal `round(source_servings × scale)`. `servings` is an
  INTEGER column, so a fractional scale rounds; the check compares against the
  rounded value rather than requiring an exact match. The tikka's 4 → 6 with
  doubled quantities is a 422.
- **Unresolved anaphora.** Any step whose `source_text` matches
  `/\b(half|rest|remaining|reserved)\b/i` and whose resolved `text` is
  byte-identical to it — meaning it was copied, not resolved — produces a
  warning naming those steps. Not a rejection: some such steps are legitimately
  unresolvable, and a false 422 would push toward fabricating a number.

## Quantity resolution

Procedure, not code. Reliable prose parsing is not achievable here, and a
half-working parser would be trusted more than it deserves.

Walk the steps in order, tracking a running balance per ingredient:

1. Source states an explicit amount → use it.
2. Source is anaphoric ("half of", "the rest") → compute from the balance.
3. Allocations must sum to the ingredient's total quantity. A mismatch means
   either the source is sloppy or it was misread — investigate, do not paper
   over.
4. Where a source is genuinely ambiguous, keep the vague wording and let the
   anaphora warning stand. Never fabricate a number.

The mechanical check (rule 4's warning) catches the case where step 1–3 were
skipped entirely, which is what happened with the oil.

## Rendering

`units.merge()` already converts within a unit family and stacks across
families; `format_quantity()` renders the result. The shopping view calls them
per pantry item. No new arithmetic.

- **Cooking view:** grouped by component, steps with resolved quantities.
  Tikka's garam masala appears twice, under Marinade and under Gravy, which is
  correct — two additions at two different times.
- **Shopping view:** flat, merged by pantry item. Garam masala is one line,
  `1¼–1½ tsp total`.

The shopping view hides `excluded` rows and **shows** `optional` ones, visually
marked. Auto-dropping optional ingredients would be omission wearing a
different hat.

## API shape

`_recipe_json` keeps its flat `ingredients` array — the store-list path reads
it — with the new fields added to each row. It gains a parallel
`components: [{name, position, ingredients: [...]}]` for the cooking view. Same
rows, two shapes, one query.

`POST /api/recipes` accepts ingredients as either strings or objects. A bare
string means `{source_text: s, raw_text: s}`, so existing tests and the two
older recipes keep working untouched.

## Error handling

| Condition | Behavior |
|---|---|
| Fetch fails, times out, or is paywalled | 502 from `/import`, naming the cause |
| No JSON-LD and no microdata | 422 from `/import`; no partial draft returned |
| JSON-LD present but no `Recipe` node | same as above |
| Unclaimed source ingredient on create | 422, unclaimed lines named |
| Servings arithmetic mismatch | 422 |
| Unresolved anaphora in a step | 200, `warnings` array in the response |

## Testing

New `tests/test_recipes_import.py`, plus additions to existing files:

- `extract()` against a trimmed tikka fixture: 26 ingredients, 15 steps, 3
  section names
- `extract()` against `@graph`-nested JSON-LD
- `extract()` with no JSON-LD raises rather than returning a half-recipe
- gate rejects an unclaimed source line, 422, names the line
- gate accepts an `excluded` row carrying a reason
- servings arithmetic mismatch → 422
- anaphora warning fires when resolved `text` equals `source_text`
- `parse.py` keeps `qty_high` on a range
- `components` grouping and flat-merged shopping output
- a bare-string ingredient payload still works (backward compat)

The fixture is trimmed to the JSON-LD block plus a minimal wrapper. The live
page is ~1 MB of ad markup and does not belong in the repo.

## Backfill

- **Tikka masala (recipe 3):** re-import through the new pipeline. It is the
  broken one and it is the proof the gate works.
- **Tempeh three-bean chili (recipe 1):** has a `source_url`; re-import
  opportunistically.
- **Broccoli, cheddar and potato soup (recipe 2):** no source. Renders through
  the `instructions` blob fallback. Unchanged.
