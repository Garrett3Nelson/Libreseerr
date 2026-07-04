# Read-frontier-aware default entry for series cards

**Date:** 2026-07-04
**Branch:** feature/hardcover-recommendation-rows
**Scope:** Frontend only (`static/js/app.js`), one function.

## Problem

`nextGapIndex` scans series entries from index 0 for the first
`!read && released && !fullyOwned` entry. Fractional installments that sit
*behind* the reading frontier (Stormlight's 0.1 "Way of Kings Prime" draft,
Old Man's War 1.5) are unread and unowned, so the card opens on them instead of
on the next thing to read. Those minor releases should be scroll-left context,
not the opening card.

## Behavior

Open each card on the first actionable entry **after the furthest whole-numbered
read**, leaving missed minor releases reachable by scrolling left.

1. **Frontier** `F` = highest integer position among entries flagged `read`
   (`Number.isInteger(e.position) && e.read`). Backend marks every whole
   position `<= furthest` as read, so this is reliable. `hasReads` = F exists.
2. **Threshold** for the default landing:
   - has reads -> `position > F`
   - no reads  -> `position >= 1` (skips 0.x prequels, lands on book 1)
3. **Primary pick:** first entry (ascending) past the threshold that is
   actionable: `!read && released && !fullyOwned`. Skips a next book you
   already own; lands on the first you could request.

### Fallback chain (each reached only if the prior finds nothing)

- **A.** First `!read` entry past the threshold, regardless of owned/released —
  covers "next books all owned or unreleased," showing the next real
  installment rather than jumping backward.
- **B.** Caught up on the whole-numbered line — nothing actionable ahead, only
  minor releases missed *behind* the frontier. Open on the furthest read whole
  book (the frontier entry) so those missed installments are reachable by
  scrolling LEFT, rather than opening the card on a 0.x prequel. (This corrects
  the original design, which reverted to "earliest actionable" here and thus
  reproduced the very behavior being fixed — real library data hits this path
  constantly, since the last released main book is usually read.)
- **-1** only when every entry is read -> card removed (unchanged).

## Non-goals / unchanged

- No backend or data-shape change; `read`, `released`, `position` already exist.
- `initSeriesCards` consumes the return value unchanged.

## Verification

- `ruff check .` / `pytest` — unaffected (no Python change) but run as gate.
- In-browser against live Hardcover data: Stormlight opens past the furthest
  whole read; scroll-left reveals 0.1 / 1.5. Visual confirmation required.
