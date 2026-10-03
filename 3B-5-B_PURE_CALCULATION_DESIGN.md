# PHASE 3B-5-B — Pure Calculation Design (proposal, not implemented)
Built from the six Discovery findings only — no new architecture assumed beyond what Discovery located. **No production file is touched by this document.** This is what would be extracted, if and when extraction is authorized; it is not the extraction itself.

Scope, per your instruction: Calculations 1–4 only. Calculation 5 (Transfer Pricing) has no independent arithmetic to design a boundary for (Discovery already established this — nothing to add here). Calculation 6 (Opening Inventory) isn't a calculation. Finding C/Returns: untouched.

---

## Design principle applied uniformly
A calculation is "pure" here in the sense already established in Discovery: no `Session`, no query execution, no I/O, no mutation of anything outside its own return value. That is orthogonal to *what type* its inputs are shaped as — Calculation 4 already proved an ORM-typed input doesn't itself break purity. So each boundary below has two separate decisions, and they are called out separately: (a) is the calculation pure, and (b) what shape should its input be. (a) was already settled by Discovery for all four. (b) is new — this document proposes an answer for it, since "no SQLAlchemy inside the calculation" doesn't automatically answer "should the parameter type be an ORM row or a plain data structure."

---

## Calculation 1 — Average Cost Accumulation

**Proposed pure boundary:**
```
def accumulate_average_cost(movements: Sequence[MovementFact]) -> ItemStockSummary
```
where `MovementFact` is a proposed minimal read-only structure carrying exactly `(direction, quantity, unit_cost)` — the three fields the accumulation loop actually reads today. `ItemStockSummary` is unchanged (already a plain dataclass per Discovery).

**Stays outside this function, unchanged, in `item_queries.py`:**
- Building and executing the SQLAlchemy query.
- **The ordering of `movements` passed in — this document does not decide it.** Today's code orders by `movement_date` alone; Finding B is still open. `get_item_stock_summary()` continues to do exactly what it does today (query, order, then feed the result into `accumulate_average_cost`) — the only change *this design allows itself to describe* is that the accumulation loop's body would move into a separate function with the same behavior, not that the ordering behavior changes at all.

**Open input-shape question (new, not a Finding-B question):** should `MovementFact` be a new plain dataclass built from each `InventoryMovement` row, or should the function simply accept `InventoryMovement` ORM instances directly and read three of their attributes? Both are equally pure by the definition above. A new dataclass is more decoupled (the pure function no longer imports `app.models` at all) at the cost of a mapping step at the call site; accepting the ORM rows directly is zero-mapping-cost but keeps a type-level dependency on the ORM inside otherwise-pure code. **Not resolved here — flagged as the one concrete decision this design needs before extraction, independent of Finding B.**

---

## Calculation 2 — Purchase Unit Cost

**Proposed pure boundary:**
```
def calculate_purchase_unit_cost(net_after_all_discounts: Decimal, exchange_rate: Decimal, quantity: Decimal) -> Decimal
```
Already exactly three Decimals in, one out, per Discovery — this is a direct extraction of existing inline arithmetic with no shape ambiguity (unlike Calculation 1, there's no ORM object involved at all today, so there's no analogous input-shape question to resolve).

**Stays outside this function, unchanged, in `posting.py`:**
- Calling `compute_invoice_totals()` to obtain `net_after_all_discounts`.
- Constructing the `InventoryMovement` row from the result.
- Everything about `post_immediate()`/the accounting boundary.

**Explicitly a new abstraction, not a rename:** per your note, this function does not exist today even inline-unnamed in the sense of being a distinguishable unit with its own name — extracting it is a Design decision being proposed now, not a discovery of something that was already conceptually separate.

---

## Calculation 3 — COGS

**Proposed pure boundary:**
```
def calculate_cogs(unit_cost: Decimal, quantity: Decimal) -> Decimal
```
Trivial by Discovery's own conclusion — the arithmetic was already pure once its two inputs are in hand. This boundary changes nothing about where `unit_cost` comes from.

**Stays outside this function, unchanged, in `posting.py`:**
- The call to `_average_cost()` (i.e., Calculation 1, via `get_item_stock_summary()`) that produces `unit_cost` — this function's DB-coupled sourcing is not addressed by extracting the multiplication.
- The per-account accumulation across an invoice's multiple lines (grouping COGS/Inventory debits by account pair) — this is invoice-posting orchestration, not part of the calculation itself, and stays in `post_sales_invoice()`.

**Explicit non-goal, stated to prevent the exact naming trap you flagged:** this is not `calculate_cogs_from_db()` and never will be — if a future phase needs `unit_cost` sourced differently (e.g. as part of a historical-recalculation pass), it still calls this same two-argument function; only the caller changes.

---

## Calculation 4 — Invoice Line Totals

**Proposed pure boundary:** none needed as new extraction — `compute_invoice_totals(invoice: Invoice) -> InvoiceTotals` already satisfies the purity definition today (no session parameter, no query in its body, confirmed in Discovery). The only design question on the table is the same input-shape question as Calculation 1's: keep the `Invoice` ORM parameter as-is, or introduce a plain `InvoiceTotalsInput` structure decoupled from the ORM.

**Recommendation, stated as a recommendation only, not adopted by this document:** resolve this the same way as Calculation 1's open question, together — if the project decides to decouple ORM types from pure-calculation signatures as a general rule, both should move together; if it decides ORM-typed inputs are acceptable as long as no I/O happens inside, neither needs to move. This is one decision, not two, and is left for whoever approves the design to make explicitly rather than defaulting to either answer here.

---

## Boundary-of-responsibility summary (who owns what, unchanged from today except where noted)

| Responsibility | Owner today | Owner after this design, if adopted |
|---|---|---|
| Fetch `InventoryMovement` rows | `item_queries.py` query | unchanged |
| Order movements (Finding B, unresolved) | `order_by(movement_date)` only | unchanged — not addressed by this design |
| Average-cost accumulation arithmetic | inline in `get_item_stock_summary` | `accumulate_average_cost()` (new pure function) |
| Purchase unit-cost arithmetic | inline in `post_purchase_invoice` | `calculate_purchase_unit_cost()` (new pure function) |
| COGS arithmetic | inline in `post_sales_invoice` | `calculate_cogs()` (new pure function) |
| Invoice line totals | `compute_invoice_totals()` | unchanged (already satisfies the goal) |
| Create `InventoryMovement` rows | `posting.py` / `returns.py` / `opening_balances.py` / `inventory_transfer.py` | unchanged |
| Create `JournalEntry` (Boundary) | `post_immediate()` / `journal_edit.reverse()` | unchanged |
| Transfer pricing (Finding D, unresolved) | `transfer_stock()`, calls Calculation 1 directly | unchanged — not addressed by this design |
| Returns cost lookup (Finding C) | `_return_unit_cost()` | untouched, out of scope entirely |

---

## What this document deliberately does not decide
- Finding B (same-`movement_date` ordering) — `accumulate_average_cost()` takes whatever sequence it's given; the ordering question is unaffected by whether the arithmetic lives inline or in a named function.
- Finding D (transfer pricing timing) — untouched; Calculation 5 has no boundary to design.
- Finding C / Returns — not part of this document at all.
- Whether historical recalculation (3B-5-C) will call `accumulate_average_cost()` directly, wrap it, or need a different function entirely — that is 3B-5-C's decision to make once it exists, not a constraint imposed by this design.

## What would need to happen before any of this is written as code
1. Explicit approval of this design (or a revised version of it).
2. A decision on the one open input-shape question (Calculation 1 / Calculation 4 — ORM-typed vs. plain-data parameters), since it affects both function signatures identically.
3. Only then: extraction, followed by characterization-test re-run (the existing 8/8 suite must still pass unchanged, since none of these extractions are meant to alter behavior), full regression, and fuzz — exactly the gate sequence already used for every prior phase.
