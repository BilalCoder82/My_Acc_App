# PHASE 3B-5-B — Existing Calculation Discovery
Scope: **discovery only**. No code written, no functions extracted, no behavior changed. This is not the pure-function design — it is the prerequisite inventory the design has to be built from. Findings A, B, and D are **not resolved** below; Finding C is **not analyzed** below (excluded per the 3B-5-A scope decision).

This document does **not** assume the shape `get_item_stock_summary() → average_cost() → COGS()` is the correct decomposition. Each calculation below was located independently by reading the actual call sites, not by starting from that assumed pipeline — and the result is a different shape than assumed (see Calculation 2 and 3: neither exists as a named function today).

---

## Calculation 1 — Average Cost Accumulation
**Where**: `item_queries.py::get_item_stock_summary()` — the only implementation in the codebase.

**Inputs**: every `InventoryMovement` row for `(item_id[, warehouse_id])`, fetched via a SQLAlchemy query ordered by `movement_date` (the ordering itself is DB-coupled and is exactly Finding B's open question — not addressed here).

**Calculation**: iterate the ordered rows; IN adds `qty×unit_cost` to a running total and `qty` to a running quantity; OUT subtracts `qty×that_row's_own_unit_cost` from both (never a re-derived average — this is an existing invariant, not a design choice being made now).

**Output**: `ItemStockSummary(quantity, average_cost, inventory_value)` — a plain dataclass, already decoupled from the ORM beyond the input.

**Existing invariants**: OUT movements valued at their own stored cost; `quantity <= 0` short-circuits to a zero-valued result; per-warehouse isolation when `warehouse_id` is given.

**Can this be isolated as a pure function preserving current behavior exactly?**
Partially, and the boundary matters: the accumulation loop itself (given any already-produced sequence of `(direction, quantity, unit_cost)` tuples) is already pure arithmetic — nothing about it depends on the session once the rows are in hand. The **query and its ordering** are the DB-coupled part, and that ordering is precisely what Finding B leaves undecided. This document does not decide who resolves that ordering or how — it only notes that "accumulate a given ordered sequence" and "fetch and order the sequence" are two separable concerns *within* today's single function, discovered by reading it, not proposed as its future shape.

---

## Calculation 2 — Purchase Unit Cost (currently unnamed, inline)
**Where**: inline inside `post_purchase_invoice()`'s per-line loop (`posting.py`) — **not a separate function today**. This is the clearest example of the pipeline assumption being wrong: nothing called "purchase cost calculation" exists as a unit anywhere in the code.

**Inputs**: `line_total.net_after_all_discounts` (invoice-currency amount, itself the output of `compute_invoice_totals()` — see Calculation 4 below, an upstream dependency not previously characterized in 3B-5-A), `invoice.exchange_rate`, and `line.quantity`.

**Calculation**: `unit_cost_after_discount = (net_after_all_discounts × exchange_rate) / quantity`, guarded against `quantity == 0`.

**Output**: a single `Decimal`, stored as the new IN movement's `unit_cost`.

**Existing invariant**: this value is always base-currency, independent of the accounting posting boundary's own currency handling for the Cash/AP line (documented in the code as a deliberately separate concern, per WORKFLOW.md §23/§30's double-conversion history).

**Can this be isolated as a pure function preserving current behavior exactly?**
Yes, trivially — it already has zero DB/session dependency; it is three Decimals in, one Decimal out. It simply isn't extracted as a named unit today; it lives inline in the posting loop.

---

## Calculation 3 — COGS (currently unnamed, inline)
**Where**: inline inside `post_sales_invoice()`'s per-line loop (`posting.py`) — also not a separate function today.

**Inputs**: `unit_cost` (the return value of `_average_cost()`, i.e. Calculation 1's output for that item/warehouse at the moment of the call) and `line.quantity`.

**Calculation**: `line_cogs = unit_cost × quantity`, accumulated per COGS/Inventory account pair across the invoice's lines.

**Output**: a `Decimal` per account, merged into the journal-line intents.

**Can this be isolated as a pure function preserving current behavior exactly?**
The COGS arithmetic itself is already pure once `unit_cost` and `quantity` are supplied. Its current caller obtains `unit_cost` through the DB-coupled Calculation 1 path, but that does not make the COGS arithmetic itself DB-dependent — the dependency is on where its input currently comes from, not on the calculation's own nature. This distinction is recorded here, not resolved.

---

## Calculation 4 — Invoice Line Totals (upstream dependency, newly surfaced)
**Where**: `invoice_calc.py::compute_invoice_totals()`. Feeds both Calculation 2 and Calculation 3 (via `net_after_all_discounts`) but was not examined in the 3B-5-A report at all — surfaced only now because Calculation 2/3's actual inputs were traced back one level.

**Inputs**: an `Invoice` ORM object (its `.lines`, each with quantity/unit_price/discount fields).

**Calculation**: per-line gross, per-line discount, subtotal, invoice-level discount, tax — pure arithmetic throughout, confirmed by its signature: **no `session` parameter at all**, no query anywhere in the function body.

**Output**: an `InvoiceTotals` dataclass.

**Can this be isolated as a pure function preserving current behavior exactly?**
Already effectively pure in behavior — its only coupling is that its input type is the SQLAlchemy `Invoice` model rather than a plain data structure, not that it performs any DB I/O. This is a narrower gap than Calculations 1–3 and is flagged here as a discovery item for whoever scopes the actual 3B-5-B extraction, not resolved.

---

## Calculation 5 — Stock Transfer Pricing
**Where**: `inventory_transfer.py::transfer_stock()`.

**Inputs**: the result of Calculation 1 for the source warehouse, called at the moment `transfer_stock()` runs.

**Calculation**: none beyond copying that value — both the OUT (source) and IN (destination) legs are priced at exactly Calculation 1's current output. There is no independent arithmetic here to isolate.

**Note**: this is *where* Finding D lives (the value used is "right now", not "as of `transfer_date`"), but this document only records that the pricing is a direct pass-through of Calculation 1's output — it does not propose when that call should happen.

---

## Calculation 6 — Opening Inventory (not a calculation)
**Where**: `opening_balances.py::post_opening_inventory()`.

**Finding**: the entered opening quantity and cost are written directly to `InventoryMovement` with no computation step at posting time. It becomes an *input* to future Calculation 1 calls, but performs no calculation itself. Recorded here only so it isn't mistaken for a missing entry in this inventory.

---

## Explicitly excluded from this document
**Finding C / `_return_unit_cost()`** — per the 3B-5-A scope decision, this is Returns-track work (RETURNS-COST-001), not part of the historical-costing design track. It is not analyzed for pure-isolation here, and no calculation-discovery conclusion should be read as applying to it.

## Summary table

| # | Calculation | Named today? | DB-coupled? | Pure-isolable as-is? |
|---|---|---|---|---|
| 1 | Average Cost Accumulation | Yes (`get_item_stock_summary`) | Yes — query + ordering (Finding B) | Accumulation step: yes. Query/ordering step: no, by design question still open |
| 2 | Purchase Unit Cost | **No** — inline only | No | Yes, trivially |
| 3 | COGS | **No** — inline only | Not itself — only its current `unit_cost` input source (#1) is | The arithmetic itself: yes, already pure |
| 4 | Invoice Line Totals | Yes (`compute_invoice_totals`) | No (ORM-typed input only) | Effectively yes already |
| 5 | Stock Transfer Pricing | Yes (`transfer_stock`) | Indirectly, via #1 | No independent arithmetic to isolate |
| 6 | Opening Inventory | Yes (`post_opening_inventory`) | N/A — not a calculation | N/A |

## What this document does not do
It does not decide the ordering question (Finding B), does not decide when Calculation 1 should be evaluated for a transfer (Finding D), does not decide how Calculation 1's COGS-dependency should be restructured, and does not extract, rename, or move any code. It is the input to a 3B-5-B design discussion, not the design itself.
