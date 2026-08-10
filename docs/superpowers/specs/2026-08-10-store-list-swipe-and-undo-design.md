# Store list — swipe to check off, undo bar to fix mistakes

**Date:** 2026-08-10
**Status:** approved, ready to implement

## The problem

On the Store List tab the whole row is one tap target (`templates/recipes.html`,
`lineRow`). A tap flips the line to checked, and a checked line immediately
leaves the screen and sinks into its section's "✓ N in the cart" fold.

That combination makes an accidental tap expensive. Scrolling a long list on a
phone in a store means dragging a thumb across the exact element that commits a
change, and there is no cheap way back: the mistake is now behind a collapsed
fold in whichever aisle it belonged to, and the row you were actually reading
has reflowed under your finger.

The other list surfaces don't have this problem and are out of scope. Pantry
rows open an editor (recoverable, non-destructive). The meal-plan chips already
sit behind a `confirm()`.

## Goals

- Checking an item off requires a deliberate gesture, not a tap.
- A mistake is reversible in one tap, without hunting through folds.
- Vertical scrolling is never captured or degraded by the new gesture.
- No API change: `/list/line/<id>/check` already accepts `checked: false`.

## Non-goals

- Swipe-to-delete, or any second swipe action per row.
- Reordering, multi-select, or a batch undo across several items.
- Changing how the folds work — the fold stays the way to fix something older
  than the undo window.

## Design

### Gesture

Pointer Events (`pointerdown` / `pointermove` / `pointerup` / `pointercancel`),
so the same code path serves touch and a trackpad drag.

- **Swipe right** on an open row commits it to the cart.
- **Swipe left** on a checked row (inside an expanded fold) puts it back.
- Each row accepts only the direction that changes its state. A swipe the wrong
  way for the row's current state does not track and does not commit.

**Axis lock.** The first ~10px of travel decides. If the vertical component
wins, the gesture is abandoned for the remainder of that pointer sequence and
the browser scrolls normally. If horizontal wins, the row captures the pointer
and tracks it. Because the row must be free to scroll until the axis is decided,
`touch-action` on `.row` becomes `pan-y` rather than `manipulation`.

**Commit threshold.** 35% of the row's width, floored at 64px. Releasing short
of the threshold springs the row back and changes nothing.

### Feedback

The row's content translates with the pointer. A green panel bearing `✓`
(`--go`) is revealed behind it, sized by the drag distance. Past the threshold
the panel goes to full opacity, so releasing-commits is legible before you let
go. Spring-back and commit-slide are CSS transitions on `transform`, disabled
while the finger is down so tracking stays 1:1.

### Tap

Tapping the row body no longer changes state. Two deliberate paths remain:

- The 26px `.bx` checkbox stays tappable and toggles the line. It is small and
  at the row's leading edge, which is not where a scrolling thumb lands. This is
  also the mouse and keyboard-accessible path.
- A tap anywhere else on the row nudges it ~8px in its actionable direction and
  springs back, revealing a sliver of the green panel. It commits nothing; it
  teaches the gesture in place of a dead tap.

### Undo bar

A fixed bar at the bottom of the viewport, above the nav, reusing the existing
`.toast` positioning with a neutral/green style instead of the red error style:

```
Cheddar — in the cart                                    Undo
```

- Lives **8 seconds**.
- Appended to `document.body`, not `#main`. The 4-second `poll()` calls
  `render()`, which rebuilds `#main` wholesale; a bar rendered inside it would
  be destroyed mid-countdown, and its timer must not be reset by a re-render
  either.
- Tracks **only the most recent** check. A new check replaces the text and
  resets the 8 seconds. Checking items in quick succession — the normal rhythm
  in a store — leaves the latest one undoable, which is the one a mis-swipe
  would have just created.
- `Undo` reverts the line locally via `markChecked`, re-renders, and POSTs
  `checked: false` through the same call the swipe used. The bar dismisses on
  undo.
- The undo bar and the existing error toast are separate elements and can both
  be on screen; the error toast keeps its own slot and lifetime.

### Error handling

Unchanged in shape. The existing optimistic flip already reverts on refusal and
surfaces `note()`. Undo travels the identical path, so a refused undo reverts
and explains itself the same way.

## Verification

The frontend is inline script in a single template; the test suite is Python and
covers the API, which this change does not touch. Verification is therefore
manual, driving the real page in Chrome at phone width:

1. Vertical scroll through a long list is unaffected — no row commits, no row
   visibly translates.
2. Short swipe right, released before threshold — row springs back, unchanged,
   no undo bar.
3. Full swipe right — row commits, sinks into its fold, count decrements, undo
   bar appears.
4. Undo within the window — row returns to its section in place, count restored.
5. Let the bar expire, then confirm the fold still holds the item and re-tapping
   its checkbox there puts it back.
6. Two checks within 8 seconds — one bar, naming the second item, undoing only
   that one.
7. A `poll()` cycle (4s) elapses with the bar up — bar survives and still
   undoes correctly.
8. Tap the row name — nudge only, nothing committed.
9. Tap the checkbox — commits, same as a swipe, with the undo bar.
