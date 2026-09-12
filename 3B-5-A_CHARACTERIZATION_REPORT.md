# PHASE 3B-5-A — Characterization Report
Scope: read-only code reconnaissance + `tests/test_characterization_3b5a.py` (5/5 PASS against the real code, no production files touched). No fixes proposed here — this is "what the system actually does today."

## 1. InventoryMovement lifecycle
`InventoryMovement` (models.py) is an append-only fact table: `item_id, warehouse_id, direction, quantity, unit_cost, movement_date, source_type, source_id`. There is **no `created_at`/insertion-timestamp column** — only the business-meaningful `movement_date`, which callers can set to any date (including a backdate). No status/void flag. It is created by four call sites: `post_sales_invoice`, `post_purchase_invoice` (posting.py), `post_sales_return`/free-return path (returns.py), opening balances/opening inventory, and the two reversal paths (`invoice_cancel.py`, `opening_balances.reverse_opening_inventory`). No code path ever `UPDATE`s or `DELETE`s an existing row (confirmed by grep + characterization check #4) — the only correction pattern in the codebase is an **additive mirror reversal** (new opposite-direction row at the same historical `unit_cost`), never in-place mutation.

## 2. Average-cost algorithm
`get_item_stock_summary()` (item_queries.py) is the **single source** for average cost — `posting.py`, `returns.py`, and `inventory_transfer.py` all call it (or its thin wrapper `_average_cost`), no duplicate implementation exists. It is a **full recompute from scratch on every call**: pulls every `InventoryMovement` row for the item (optionally filtered by warehouse), orders by `movement_date`, and accumulates `total_qty`/`total_cost` — IN adds `qty*unit_cost`, OUT subtracts `qty*movement.unit_cost` (the OUT's own stored historical cost, not a re-derived average). Final `avg = total_cost/total_qty`. There is **no cached running balance, no incremental update, no per-date snapshot** — confirmed by characterization check #1.

## 3. COGS calculation
`post_sales_invoice` calls `_average_cost()` **before** adding the new OUT movement, uses that value as `unit_cost` for the new movement, and books `COGS = unit_cost × qty` via `_jline_base` (already-base-currency path — this is the exact quantity that the §29/§30 double-conversion bug attacked previously, now fixed and regression-locked). COGS is computed once, at posting time, and never revisited afterward for that document.

## 4. How cost is affected by an old movement
Because average cost is a full recompute over *all* rows ordered by `movement_date` at the moment of the call, a movement dated in the past **does** affect the average returned by the next call — but only for movements *not yet posted*. Any movement already posted **before** the backdated one was inserted keeps its own already-stored `unit_cost` forever (see item 8).

## 5. Editing/canceling/deleting historical movements
There is no direct edit or delete of `InventoryMovement`. The only sanctioned "undo" mechanisms are: (a) `invoice_cancel.py::cancel_invoice()` — literal reversal at the original invoice's own movements' exact historical cost, blocked if any `Settlement` is linked; (b) `opening_balances.py::reverse_opening_inventory()` — same additive-mirror pattern, **with a guard** (see item 14). Editing a POSTED invoice's line items directly is blocked entirely by `invoice_edit.py::ensure_editable()` — the documented-only path is cancel-then-recreate.

## 6. Ordering movements chronologically
Sorting key is `InventoryMovement.movement_date` **only** — `item_queries.py`: `query.order_by(InventoryMovement.movement_date)`. No secondary tie-breaker (no `id`, no `created_at`) for same-date rows. Characterization check #2 confirms the query has no such tiebreaker; current same-date behavior happens to match insertion order under SQLite's unindexed scan, but that is incidental, not contracted.

## 7. Warehouse effect
Fully isolated per `(item_id, warehouse_id)` since §46 — `_average_cost()` takes no default for `warehouse_id`, forcing every accounting call site to be explicit. `warehouse_id=None` is accepted only by the two UI display call sites (`item_card_dialog.py`, `item_list_view.py`) for an aggregate "total across all warehouses" view — never used by a posting/costing decision. `inventory_transfer.py` correctly uses the *source* warehouse's own average for the transfer-out leg.

## 8. Quantity/unit_cost effect
Confirmed concretely by characterization check #3: a purchase dated *before* an already-posted sale, but **entered into the system after** that sale was posted, does not retroactively touch the sale's stored `unit_cost` or its journal entry. The sale keeps the average that existed at its own posting time. This is the literal reproduction of the 3B-5 problem statement, not a hypothetical.

## 9. Purchase/sales returns impact
Linked returns read cost from the **original movement itself** (`returns.py::_return_unit_cost`, exact match via `source_type in (sales_invoice, purchase_invoice)` + `source_id`) — historically accurate by construction. Free (unlinked) returns fall back to the *current* average at return time — documented as a deliberate, acceptable approximation (no original document to be precise against). **Known gap not previously flagged**: the linked-return lookup uses `.first()` on `(source_id, item_id)` — if one invoice has two separate lines for the same item (legal today, nothing prevents it), the return will silently pick whichever movement the query happens to return first, not necessarily the line actually being returned.

## 10. Opening inventory impact
Opening inventory postings are indistinguishable from a normal IN movement to the costing algorithm — same table, same accumulation. The only place opening balances get special treatment is the reversal guard (item 14).

## 11. Any path that can change a POSTED historical movement's result
None directly (no update/delete). Indirectly: inserting a **new** movement with an earlier `movement_date` changes what `get_item_stock_summary()` will return for *future* calls, but never retroactively changes a value already written to a prior `InventoryMovement.unit_cost` or a prior `JournalEntry`. This asymmetry — past unposted-order inserts affect the future, never the past — is the core fact 3B-5-B/C has to design around.

## 12. Existing recalculation mechanisms
There is exactly **one** codified "would this recalculation break something already relying on it" guard in the entire codebase: `opening_balances.py::reverse_opening_inventory()`, which blocks the reversal if any OUT movement exists for that `(item, warehouse)` dated on/after the opening date. It is scoped narrowly to opening-inventory reversal only. There is **no equivalent guard** anywhere for cancelling/editing a mid-stream purchase invoice that a later sale's average already depended on (confirmed by check #5) — `cancel_invoice()` will happily reverse a purchase even if a sale already consumed stock priced from it.

## 13. All callers
`get_item_stock_summary`: `posting.py` (`_average_cost`), `returns.py` (`_average_cost`, `_return_unit_cost` fallback), `inventory_transfer.py`, plus two UI display call sites (aggregate view only, `warehouse_id=None`). No other caller exists in `app/services` or `app/ui`.

## 14. Current transaction boundaries
Posting functions (`post_sales_invoice`/`post_purchase_invoice`) do an explicit `session.commit()` mid-function (§Group 3-B fix, to avoid the documented SQLite lock issue with `post_immediate`'s independent ref-no reservation transaction), then build everything else in memory and commit again atomically in the `try/except` around `post_immediate`. `InventoryMovement` rows are only `session.add()`-ed **after** `post_immediate()` succeeds, specifically to avoid orphaned movements on a failed journal post. This ordering matters for 3B-5-B/C: any new "impact analysis" step that reads current stock state must do so either fully before this mid-function commit or account for the fact that a stale read across it is possible in theory (not observed as a bug, just a structural note).

## 15. Current performance characteristics
`get_item_stock_summary()` is **O(n) in total historical movement count for that item(/warehouse)**, on every single call, with no caching. It already re-runs on every invoice line at posting time (once per line via `_average_cost`), and again on every return, transfer, and UI display of the item. For a client with years of high-volume movements per item, this will get measurably slower over time — not a bug today, but relevant input to 3B-5-C's "do we need a recalculation policy / background job" question, per the explicit "not now, only when size actually demands it" instruction.

## Identified invariants (confirmed, not assumed)
- InventoryMovement is append-only; no code path mutates a posted row in place.
- OUT movements are always valued at their own stored `unit_cost`, never a re-derived average (§39, re-verified live).
- Average cost is isolated per `(item, warehouse)`, with `None` reserved for display-only aggregation.
- Linked returns reprice from the exact original movement; unlinked returns use current average by design, documented as an approximation.

## Known gaps (newly surfaced by this reconnaissance, not previously documented)
1. No secondary sort key for same-`movement_date` rows in the costing query (item 6).
2. `_return_unit_cost`'s linked-return lookup can pick the wrong line's movement when an invoice has duplicate item lines (item 9).
3. The "later movement already depended on this" guard exists only for opening-inventory reversal, not generalized (item 12) — this is precisely the gap 3B-5-C is being opened to close.
4. No historical audit trail of *who/when* entered a backdated movement, or that it was backdated at all, beyond the `movement_date` itself — relevant to whatever 3B-5-C decides about detecting and surfacing historical corrections to the accountant.

## Proposed 3B-5 boundaries (informational only — decision stays with you and the programmer)
Reconnaissance surfaced nothing that changes your original plan; A/B/C/D as scoped in your message look consistent with what's actually in the code. The one thing I'd flag before 3B-5-C is designed: gap #3 above (missing guard) and gap #2 (duplicate-item-line return ambiguity) are two **separate risk classes** — the first is "recalculation policy" (your 3B-5-C), the second is a plain correctness bug candidate unrelated to historical corrections at all. Worth deciding explicitly whether #2 is in scope for 3B-5 or tracked separately, so it doesn't get silently folded into (or lost inside) the bigger historical-correction design.

---
### A coach's note, not a compliance note
You told the programmer "لا نصلح شيئاً لأننا نعتقد أنه خاطئ قبل أن نثبت أولاً" — and this reconnaissance vindicates that instinct: item 8 (the backdated-purchase gap) is exactly the kind of thing that's tempting to "just fix" and would have quietly changed historical numbers without anyone deciding it should. Two things worth pressure-testing before 3B-5-B starts writing pure functions:

- You've scoped out background jobs and Alembic until proven necessary — reasonable — but you haven't yet named *who decides* "proven necessary" for 3B-5-E, or what number (movement count? recompute latency?) triggers it. Leaving that as a subjective future call is the same kind of ambiguity you explicitly rejected for rounding thresholds back at Phase 3B-1's fuzz gate. Consider fixing a number now, cheaply, even if it's provisional.
- The moving-average method itself (see [Investopedia's overview](https://www.investopedia.com/terms/a/averagecostmethod.asp) for the standard treatment) is defined against chronological order of transactions — your system currently has no reliable definition of "chronological" for same-day entries (gap #1) and no guard against inserting one that's earlier than something already relied on (gap #3). 3B-5-C is the right place to fix this, but it's worth naming explicitly as *the* reason 3B-5-C exists, not just "historical corrections" in the abstract — that framing will help the programmer see why the guard has to be general, not another opening-inventory-shaped special case.
