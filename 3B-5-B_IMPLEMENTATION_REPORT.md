# PHASE 3B-5-B — Implementation Report

## Files changed (exactly these two, nothing else)
- `app/services/item_queries.py`
- `app/services/posting.py`

## Functions added
```python
# app/services/item_queries.py
@dataclass(frozen=True)
class MovementFact:
    direction: MovementDirection
    quantity: Decimal
    unit_cost: Decimal

def accumulate_average_cost(movements: list[MovementFact]) -> ItemStockSummary: ...

# app/services/posting.py
def calculate_purchase_unit_cost(net_after_all_discounts: Decimal, exchange_rate: Decimal, quantity: Decimal) -> Decimal: ...
def calculate_cogs(unit_cost: Decimal, quantity: Decimal) -> Decimal: ...
```
Per the approved input-shape decision: `MovementFact` is plain data (no ORM/SQLAlchemy import) for Calculation 1. Calculations 2 and 3 were already Decimal-in/Decimal-out with no ORM involvement, so no analogous type existed to decide on.

## Functions unchanged
- `compute_invoice_totals()` (`invoice_calc.py`) — **not touched at all**, per the explicit decision to keep its `Invoice`-typed input as-is (no `InvoiceTotalsInput` introduced).
- `_average_cost()` — still calls `get_item_stock_summary()` exactly as before; its own body is unchanged.
- `get_item_stock_summary()` — signature, query, and `order_by(InventoryMovement.movement_date)` ordering are byte-for-byte unchanged. Its body now builds a list of `MovementFact` from the queried rows and delegates the arithmetic to `accumulate_average_cost()` instead of looping inline.
- `post_sales_invoice()` / `post_purchase_invoice()` — unchanged except the two one-line replacements below; everything else (transaction handling, `post_immediate()` calls, journal-line construction, movement construction) untouched.
- `returns.py`, `inventory_transfer.py`, `opening_balances.py`, `invoice_cancel.py` — **not opened, not touched.**

## Exact substitutions made
1. `post_sales_invoice()`: `line_cogs = money(unit_cost * D(line_total.line.quantity))` → `line_cogs = calculate_cogs(unit_cost, D(line_total.line.quantity))`. `calculate_cogs()`'s body is `money(unit_cost * quantity)` — identical arithmetic, identical rounding call, same argument values.
2. `post_purchase_invoice()`: the two-line `net_in_base = money(...); unit_cost_after_discount = (net_in_base / q) if q else Decimal("0")` → single call `calculate_purchase_unit_cost(line_total.net_after_all_discounts, D(invoice.exchange_rate), q)`, whose body performs the identical two steps in the identical order (round via `money()` first, then divide, same zero-quantity guard).
3. `get_item_stock_summary()`: the inline `for m in movements: ...` accumulation loop → build `MovementFact` list, call `accumulate_average_cost(facts)`. `accumulate_average_cost()`'s body is the original loop verbatim, operating on `.direction/.quantity/.unit_cost` instead of `m.direction/D(m.quantity)/D(m.unit_cost)` (the `D()` conversion now happens once when building each `MovementFact`, not per-arithmetic-operation — same values, same `Decimal` type, same result).

## Behavior preservation — what was proven, not assumed
- **8/8 characterization tests pass unchanged** against the modified code, including Test A (late historical purchase), same-date-ordering check, warehouse-isolation check, stock-transfer check, and the returns multi-line check — none of these were touched by this extraction, and none needed modification to keep passing.
- **Full Regression: 42/42 PASS, 0 failures.**
- **Fuzz: 200/200 PASS, 0 failures**, rounding-diff threshold unchanged (derived from `MONEY_QUANT`, not a fixed number).
- No test file was modified to make anything pass.

## Tests
- 3B-5-B characterization: **8/8**
- Full Regression: **42/42**
- Fuzz: **200/200**

## Scope confirmation
- **Finding B (same-date ordering): unchanged / unresolved.** `order_by(InventoryMovement.movement_date)` is untouched; no secondary sort key was added; `accumulate_average_cost()` has no opinion on ordering — it processes whatever sequence it's given, exactly as the inline loop did before.
- **Finding D (stock-transfer costing timing): unchanged / unresolved.** `inventory_transfer.py` was not opened.
- **Finding C (Returns / `_return_unit_cost()`): untouched.** `returns.py` was not opened.
- **Historical Correction: not implemented.** No recalculation, repricing, or correction-propagation logic was added anywhere.
- **No architecture added beyond the two approved functions and one data class** — no Repository, UoW, DDD layer, DI, DTO framework, generic costing abstraction, strategy pattern, factory, or event system.
- **`compute_invoice_totals()`: confirmed unchanged**, per the explicit decision not to introduce `InvoiceTotalsInput` in this phase.

## Status
PHASE 3B-5-B — Pure Calculation Extraction: **COMPLETE**. Ready for your review before any decision to proceed to 3B-5-C.
