# PHASE 3B-5-A — Characterization Report (v2, full scope)

Scope: read-only reconnaissance of the actual code in this archive + `tests/test_characterization_3b5a.py` (8/8 PASS, re-run against this exact tree before delivery). **Zero production code changes.**

## 0. Version reference status
This report and its tests are built directly against `My_Acc_App_PHASE3B4_CLOSED.zip` as supplied. §70 in `WORKFLOW.md` already documents, in its own words, that `returns.py`, `settlements.py`, `opening_balances.py::post_opening_inventory/reverse_opening_inventory`, and `posting.py::post_return` were **deliberately out of scope for 3B-4 from the start** (post_return confirmed here as dead code, zero call sites). No documentation gap exists — the earlier concern about an undocumented scope boundary does not apply to this archive.

## 1. InventoryMovement lifecycle
Append-only fact table (`item_id, warehouse_id, direction, quantity, unit_cost, movement_date, source_type, source_id, note`). No status/void flag, no `created_at`. `movement_date` is typed `DateTime` in the schema, but every caller feeds it a plain `date` (Invoice.invoice_date is `Date`) — so in practice the time component is always midnight; the column's extra time resolution is never actually used as an implicit ordering signal. Created by exactly six call sites: `post_sales_invoice`, `post_purchase_invoice`, `post_sales_return`, `post_purchase_return`, `post_opening_inventory`, `transfer_stock`. Never updated or deleted anywhere (confirmed by grep, check #4); the only "undo" pattern is an additive mirror-reversal (`invoice_cancel.py`, `opening_balances.reverse_opening_inventory`). No path allows creating a movement with an invalid/missing source — `source_type`/`source_id` are always set by the six creators, never left null except by construction contract (opening balances use a fixed `source_type` with no originating document). A movement can always be created with a `movement_date` earlier than existing POSTED movements — nothing in the schema or any creator function checks this.

## 2. Average-cost algorithm
`get_item_stock_summary()` (item_queries.py) is the single implementation — full recompute from scratch on every call, no cache/running balance (check #1). Available quantity and value are accumulated by iterating every `InventoryMovement` row for `(item_id[, warehouse_id])` ordered by `movement_date` only: IN adds `qty×unit_cost`; OUT subtracts `qty×that_movement's_own_stored_unit_cost` (never a re-derived average). Opening inventory, purchases, sales-return-ins, and transfer-ins are all just IN rows to this algorithm — indistinguishable by source once created. Multiple warehouses are isolated only because every accounting call site passes an explicit `warehouse_id`; `None` is accepted solely by two UI-display call sites for an aggregate view, never for a costing decision (confirmed, check #6 proves zero cross-warehouse leakage).

## 3. What happens with several movements sharing item+warehouse+movement_date (Finding B)
The query is `order_by(InventoryMovement.movement_date)` with **no secondary key**. Confirmed by check #2 and #7's setup: two same-date movements are processed in whatever order the DB returns them for a tied sort key — which happens to match insertion order under SQLite's simple scan in every case tested here, but nothing in the query enforces it. `id` **is** available (auto-increment primary key) and, empirically, tracks insertion order — but insertion order is not necessarily business-chronological order (e.g., a backdated correction entered later still gets a higher `id`). No `created_at` or independent sequence column exists. **This is a design decision to make before Historical Recalculation, not something to default to `id` for without a conscious choice.**

## 4. COGS
`post_sales_invoice` calls the average-cost function **before** adding its own OUT movement, uses that value as the new movement's `unit_cost`, books `COGS = unit_cost × qty` in base currency via `_jline_base`. Computed once, at posting time, never revisited. **No existing mechanism recalculates COGS after a historical movement is inserted** (Finding A, below). If a historical purchase changes what the "true" average should have been, the already-POSTED sale's COGS and journal entry are simply never touched — confirmed live, not inferred (check #3).

## 5. Purchase cost
`unit_price` on an `InvoiceLine` is entered in the invoice's own currency; posting converts to base currency via `exchange_rate` before it becomes `InventoryMovement.unit_cost` — `unit_cost` is always stored in base currency, confirmed by reading `_jline_base` usage in `post_purchase_invoice`. A later/historical purchase never retroactively recomputes anything already posted (same asymmetry as COGS — it only affects what future `get_item_stock_summary()` calls return).

## 6. Returns cost (Finding C)
Linked returns (`_return_unit_cost`) query `InventoryMovement` by `(source_type, source_id, item_id)` and take `.first()`. **Confirmed live** (check #8): an invoice with two separate lines for the same item produces two distinct `InventoryMovement` rows sharing the same `(source_id, item_id)` key, and `_return_unit_cost()` returns whichever one the DB happens to return first — with no line-level disambiguation. This is a plain correctness issue, independent of Historical Corrections; recorded as **RETURNS-COST-001**, not absorbed into 3B-5 scope. Unlinked (free) returns fall back to the current average at return time — a documented, deliberate approximation, not a bug.

## 7. Opening inventory
Structurally identical to a purchase IN as far as the costing algorithm is concerned. Idempotent at the whole-company level (`Setting['opening_inventory_posted_at']` — one shot for the entire company, not per item/warehouse), matching `post_opening_account_balances()`'s pattern exactly. Its only special treatment anywhere is the reversal guard (item 9 below).

## 8. Stock transfer (Finding D — new, not previously flagged)
`transfer_stock()` creates an OUT at the source warehouse and an IN at the destination, both priced at the source warehouse's **current** average cost at the moment of the transfer call — never a point-in-time average as of `transfer_date`. **Confirmed live** (check #7): a transfer backdated to a date when the true average was 10 is still costed at 15 (today's average, after a later purchase changed it). This is the exact same historical-costing asymmetry as Finding A (purchases/sales), but for an internal movement — worth folding into the same 3B-5-C design discussion rather than treated as unrelated. No `JournalEntry` is ever created for a transfer (confirmed: `inventory_transfer.py` imports neither `JournalEntry` nor `post_immediate`) — it is a pure inventory-quantity/cost movement with zero accounting-boundary involvement.

## 9. Warehouse isolation
Confirmed live (check #6): identical item, cost 10 in warehouse A vs. 999 in warehouse B, each warehouse's average reflects only its own movements — no leakage either direction.

## 10. Historical Purchase — REQUIRED CHARACTERIZATION (Finding A, the core finding)
Scenario reproduced exactly as specified and confirmed live (check #3):
- Opening/purchase at T2, average = 10.
- Sale at T3 (T3 > T2), POSTED, COGS booked at unit_cost = 10.
- A purchase entered later (in real time) but dated T1 < T3, at a very different price.

Result:
- The Sale's stored `InventoryMovement.unit_cost` and its `JournalEntry` **do not change** — confirmed unchanged before/after.
- The old purchase's `InventoryMovement` doesn't change; the new T1 purchase's own movement is created normally.
- No automatic recalculation occurs anywhere — there is no code path that reacts to a movement being inserted with an earlier date than existing POSTED movements.
- **No historical-correction mechanism currently exists.** The only "correction" primitive in the whole codebase is additive mirror-reversal of an entire document (cancel/reverse), never a targeted re-price of downstream effects.
- Final state of POSTED documents: unchanged and self-consistent with their own posting-time inputs, but no longer consistent with what a strict chronological average-cost method would say in hindsight. This divergence is permanent until a 3B-5-C mechanism is designed.

## 11. POSTED Immutability
`invoice_edit.py::ensure_editable()` blocks direct edits to a POSTED invoice's lines outright — the only sanctioned path is cancel (additive reversal) + re-create. No code path mutates a POSTED `JournalEntry` in place anywhere in the services layer (confirmed by the same grep used for check #4, extended to `JournalEntry`). Any future Historical Correction mechanism therefore has no existing "quiet edit" precedent to build on or accidentally imitate — a corrective accounting effect, if needed, would have to go through the same accounting-posting boundary as everything else (`post_immediate`/`reverse`), not a direct field mutation. This constraint is not new work to build; it's an existing invariant to preserve.

## 12. Production callers (full inventory)

| File | Function | Responsibility | Transaction ownership |
|---|---|---|---|
| posting.py | `post_sales_invoice` | Sale posting: average-cost read → OUT movement → COGS via `post_immediate` | mid-function `session.commit()` (ref-no lock avoidance), then atomic try/except around `post_immediate` |
| posting.py | `post_purchase_invoice` | Purchase posting: IN movement (base-currency cost) → `post_immediate` | same pattern as above |
| posting.py | `_average_cost` | Thin wrapper over `get_item_stock_summary` | none (read-only) |
| posting.py | `post_return` | **Dead code** — zero callers anywhere | n/a |
| returns.py | `post_sales_return` / `post_purchase_return` | Return posting, own direct `JournalEntry`+`_next_ref_no` (pre-Boundary, out of 3B-4 scope by design) | caller owns commit/rollback (older contract) |
| returns.py | `_return_unit_cost` | Linked-return cost lookup (`.first()`, Finding C) / unlinked fallback to current average | none (read-only) |
| returns.py | `get_returnable_lines` | Same `.first()`-style pattern for quantity already returned | none (read-only) |
| item_queries.py | `get_item_stock_summary` | The one and only average-cost/COGS-input calculation | none (read-only), O(n) per call, no cache |
| inventory_transfer.py | `transfer_stock` | Internal movement, current-average pricing (Finding D), no accounting effect | own `session.flush()`, no commit |
| opening_balances.py | `post_opening_inventory` | Company-wide, one-shot IN movements | caller owns commit/rollback |
| opening_balances.py | `reverse_opening_inventory` | Additive mirror-reversal, **guarded** (item 13) | caller owns commit/rollback |
| invoice_cancel.py | `cancel_invoice` | Uses `journal_edit.reverse()` (the Boundary) for the accounting side, plus its own inventory-reversal movements | atomic try/except |
| item_card_dialog.py / item_list_view.py (UI) | display only | Call `get_item_stock_summary` with `warehouse_id=None` for an aggregate view | n/a |

## 13. Existing recalculation / historical-safety mechanisms
Exactly one exists in the whole codebase: `opening_balances.py::reverse_opening_inventory()` blocks its own reversal if any OUT movement exists for that `(item, warehouse)` dated on/after the opening date. Scoped narrowly to opening-inventory reversal only — **no equivalent guard exists for cancelling a purchase invoice that a later sale's average already depended on**, nor for the transfer asymmetry in Finding D.

## Characterization Test Results
`tests/test_characterization_3b5a.py` — 8/8 PASS against this exact archive:
1. Average cost = full O(n) recompute, no cache.
2. No secondary sort key for same-`movement_date` rows.
3. **Test A** — late historical purchase does not retroactively touch an already-posted sale's COGS/unit_cost.
4. No update/delete of any `InventoryMovement` anywhere.
5. The one existing "later movement depends on this" guard is scoped to opening-inventory only.
6. Warehouse isolation — zero leakage between warehouses for the same item.
7. Stock-transfer backdating prices at today's average, not the historical one (Finding D).
8. **Test C** — multi-line-same-item invoice makes `_return_unit_cost()`'s `.first()` pick an arbitrary line (RETURNS-COST-001).

(Test B — same-date ordering — is check #2 above; no independent tiebreaker exists to test beyond confirming its absence.)

## Findings (kept explicitly separate as requested)
- **Finding A** — A late historical purchase does not currently trigger retroactive recalculation of already-posted downstream COGS. This is the core problem 3B-5-B/C exists to solve.
- **Finding B** — Same-date `InventoryMovement` ordering is not yet established as a deterministic *business* ordering (only an incidental DB-scan order). Must be resolved before designing Historical Recalculation; do not default to `id` without a conscious decision.
- **Finding C** — `_return_unit_cost().first()` is ambiguous when an original invoice has multiple lines for the same item. **Returns correctness, not a 3B-4 or Historical-Correction issue** — recommend tracking as an independent bug ticket (RETURNS-COST-001) so it doesn't get silently folded into or lost inside the larger 3B-5 design.
- **Finding D** (new) — A backdated stock transfer is costed using the source warehouse's current average rather than the historical average applicable at the transfer date. Independent of Finding A, but the same conceptual category of historical-costing problem — recommend folding into the same 3B-5-C design discussion rather than treating transfers as a separate problem later.

## Finding C scope clarification
Finding C (RETURNS-COST-001) is intentionally excluded from PHASE 3B-5-B, 3B-5-C, and 3B-5-D.

This finding concerns Returns correctness and line-level identification: `_return_unit_cost()` cannot unambiguously identify the originating inventory movement when an original invoice contains multiple lines for the same item. It is therefore tracked as an independent Returns bug and is not a Historical-Correction design problem.

Accordingly:
- 3B-5-B does not design or resolve Finding C.
- 3B-5-C does not design or resolve Finding C.
- 3B-5-D does not design or resolve Finding C.
- No historical-recalculation architecture should be shaped around Finding C.
- No production change is authorized for Finding C in the 3B-5 phases.
- Finding C remains recorded for future dedicated Returns work.

Only Findings A, B, and D belong to the current historical-costing design track:
- **A** — Historical Purchase → downstream COGS.
- **B** — Historical movement ordering (same-`movement_date` ties).
- **D** — Historical Stock Transfer Costing.

## Accounting invariants (confirmed, not assumed)
- InventoryMovement is append-only; nothing mutates a posted row in place.
- OUT movements are always valued at their own stored `unit_cost`, never a re-derived average.
- Average cost is isolated per `(item, warehouse)`; `None` is display-aggregation only, never a costing input.
- POSTED `JournalEntry`/invoice lines are never edited in place — cancel-and-recreate or additive-reversal only.
- Linked returns reprice from the exact original movement (when the lookup isn't ambiguous per Finding C); unlinked returns use current average by explicit design.

## Open design decisions (for 3B-5-B/C/D; Finding C is explicitly excluded)
1. What deterministic secondary ordering key resolves Finding B — `id`, a new independent sequence, or `(movement_date, document_type priority, document_id)`? Needs a business-semantics decision, not just a SQL fix.
2. What does "historical correction" mean accounting-wise for Finding A/D — recompute-only, a correction journal entry, blocking the edit outright until a certain point, or a policy that varies by how far downstream the impact reaches?
3. Should Finding D (transfers) be corrected by the same mechanism as Finding A, or does the "no accounting effect" nature of transfers justify a lighter-weight fix?
4. Period Lock policy (3B-5-D) needs Open/Closed period and Correction-date definitions before implementation — not addressed here by design.
5. Background-job trigger is now fixed as: P95 > 2.0s on a realistic on-disk-SQLite benchmark, OR ≥50,000 affected `InventoryMovement` rows in a single correction (a mandatory-benchmark trigger, not an automatic background-job verdict either way).

## Proposed scope for 3B-5-B
The proposed scope for 3B-5-B is limited to identifying and isolating the currently existing inventory-cost calculations that can be expressed as pure functions, without changing their current behavior or resolving any of the open characterization findings.

This includes identifying the calculation inputs, outputs, and existing invariants for calculations such as Average Cost, COGS, purchase cost, and other currently deterministic cost calculations where applicable.

No conclusion is made here regarding:
- ownership of inventory-movement ordering;
- resolution of Finding B (same-date ordering);
- resolution of Finding D (historical stock-transfer costing);
- historical recalculation or correction strategy;
- transaction/workflow ownership;
- background processing;
- period locking.

Those remain open design questions to be addressed only after the characterization findings have been formally reviewed and the relevant design decisions are made.

## Explicitly not done in this phase (per your prohibition list)
No change to the average-cost algorithm, COGS, Returns, `_return_unit_cost()`; no migration, period lock, historical-correction engine, background job, Boundary changes, service-layer redesign, Repository/UoW/DDD/DI, or start of 3B-5-B/C. This report is Characterization + Documentation-confirmation + Permanent Tests only.
