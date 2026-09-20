# PHASE 3B-5-D — Final Consistency Review, and PHASE 3B-5-E Instructions

## Consistency review (against real code and schema, not recollection)
Ten schema-level facts and four behavioral facts that Clusters 1–6 relied on were re-checked directly against `app/models.py` and the service files — not assumed to still hold from earlier in the conversation. All ten schema checks and all four behavioral checks confirmed unchanged:

- `CorrectionEvent`'s fields, `approved_candidate_state_id`'s FK-less status (the documented deviation, PD-001 — still exactly that, not silently patched), the `uq_candidate_one_assumption_per_tiegroup` constraint, and the 6-A gap (`CorrectionEventAccountingCorrection` still carries only `correction_event_id`/`journal_entry_id`, confirmed still open, not fixed by accident) — all match Cluster 6's own description exactly.
- `accumulate_average_cost()` still never re-derives an OUT's cost from the running total (Cluster 3's entire premise).
- `inventory_transfer.py` still writes one computed `unit_cost` onto both the OUT and IN rows (Cluster 3's finding that Baseline needs no cross-warehouse scheduling).
- `invoice_cancel.py`'s `cancel_date` is still a free parameter, not hardcoded to "today" (Cluster 1's finding that Detection needs no `source_type`-specific branch).
- `is_balanced()` is still enforced before any journal entry posts (Cluster 6's "mechanical half already guaranteed" claim).

**Full gate re-run**: 8/8 characterization (3B-5-A), 17/17 schema characterization (3B-5-C), 42/42 full regression, 200/200 fuzz — all PASS. **No contradiction found between the 3B-5-D design and the actual 3B-5-C schema or the 3B-1–3B-4 code it depends on.** 3B-5-D is closed with zero open inconsistencies — only the one already-registered, non-blocking gap (6-A).

## PHASE 3B-5-E — scope, closed and explicit

**What 3B-5-E is**: the first actual implementation slice of Detection logic — and *only* Detection (Cluster 1), nothing from Clusters 2–6.

**What 3B-5-E must build:**
1. The `Session.after_flush` hook (Cluster 1, Q2) — attached once, at session-configuration level, not per call site.
2. The supporting index: `INDEX(item_id, warehouse_id, movement_date)` on `inventory_movements` — a small, additive migration, matching the exact column order justified by the query shape in Cluster 1.
3. The `before_flush`-captured "newly-flushed movement IDs" exclusion set, to correctly implement the same-flush fix (Cluster 1's committed-vs-flushed correction) — sibling movements from one multi-line document must never flag each other.
4. On a Detection Candidate: write a `CorrectionEvent` row with `status = CANDIDATE` and one `CorrectionEventRootCause` row (`component_type` inferred from the triggering movement's own `source_type` — a `REVERSAL` from `invoice_cancel`, `NEW_MOVEMENT` otherwise) — using either the `after_commit`-deferred write or the raw-`Connection` approach flagged in Cluster 1 as the two candidate patterns (this slice must pick one and justify it against the nested-flush hazard, not defer that choice again).

**What 3B-5-E must explicitly NOT build — the boundary that matters most:**
- No Impact Scope graph construction (Cluster 2) — a `CorrectionEvent` created here has root causes only, no `ImpactScopeElement`/`ImpactScopeRelationship` rows.
- No recalculation walk, no `WalkAccumulator`, no `MovementFact`-based simulation (Cluster 3).
- No `TieGroup`/`OrderingAssumption`/`CandidateState` generation of any kind (Cluster 4) — a Detection Candidate is not yet an analyzed one.
- No `scope_completeness`, `chronology_basis`, or Candidate Coverage logic (Clusters 5–6) — these fields stay `NULL` on every row this slice creates.
- No `WITHDRAWN` detection logic yet — that requires knowing when a root cause's own triggering condition later disappears, which depends on Cluster 1's same detection mechanism being extended, but is deferred to its own follow-up slice so this one stays reviewable in isolation.
- No UI, no approval workflow, no `AccountingCorrection` of any kind.

**Required tests for this slice — the four already specified in Cluster 1, verbatim, not reduced:**
1. **Coverage** — post through each of the nine `InventoryMovement`-creating paths individually; the hook must fire for all nine.
2. **Negative** — a movement with no later-dated sibling for its `(item_id, warehouse_id)` produces no `CorrectionEvent`.
3. **Same-flush** — multiple movements from one flush never flag each other.
4. **Reversal** — a backdated `cancel_date` triggers detection through the identical mechanism, with no `source_type` branch in the hook itself.

Plus the existing gate, unchanged: 8/8, 17/17, full regression, fuzz — all must still pass after this slice, exactly as required for every phase before it.

**Explicit reminder carried from PD-001**: if this slice's own implementation surfaces a *second* circular-dependency-shaped problem, PD-001's resolution (drop the FK) is not to be copied automatically — each case gets evaluated on its own terms.

**Status**: 3B-5-D — CLOSED. 3B-5-E — scope authorized as above, implementation not yet started.
