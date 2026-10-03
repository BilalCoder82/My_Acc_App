# PHASE 3B-5-C — Correction Event Schema: Implementation Report

## Scope executed (schema only, per your explicit boundary)
Migration → SQLAlchemy Models → DB Constraints/Indexes → Characterization/Schema Tests. **No Detection/Analysis/Recalculation logic exists anywhere in this change** — that remains a separate, later phase.

## Files changed
- `app/models.py` — ten new model classes + six new `Enum` classes appended after `Setting`, before `create_company_database()`. No existing class modified.
- `alembic_migrations/versions/a1b2c3d4e5f6_add_correction_event_schema.py` — new migration, `down_revision = 'e5f9a3c7d2b1'` (the actual head, confirmed by tracing the full revision chain rather than assumed from directory listing order).

## Files added
- `tests/test_correction_event_schema.py` — 17 schema-level characterization checks.

## New tables (10)
`correction_events`, `correction_event_root_causes`, `correction_event_tie_groups`, `correction_event_ordering_assumptions`, `correction_event_candidate_states`, `correction_event_candidate_ordering_links`, `correction_event_delta_records`, `correction_event_impact_scope_elements`, `correction_event_impact_scope_relationships`, `correction_event_accounting_corrections`.

Every entity, field, cardinality, and constraint matches the specification in `3B-5-C_DISCOVERY_DESIGN_QUESTIONS.md` (Q3.6-A/B/C + Pre-Implementation Schema Audit) exactly — no field was added or omitted relative to that document.

## Conventions reused, not invented (verified in the existing codebase before use)
- Integer autoincrement `id` primary keys — matches every existing table.
- `source_type` (string) + `source_id` (nullable int, no true FK) polymorphic-reference pattern — same as `InventoryMovement`/`JournalEntry`'s existing origin references. Used for `RootCauseComponent` and `ImpactScopeElement`, both genuinely polymorphic.
- `ForeignKey("journal_entries.id"), unique=True` — same pattern as `Settlement.journal_entry_id` and `OpeningBalance`, reused verbatim for `AccountingCorrection.journal_entry_id`.
- `Enum` columns over raw string+CHECK — matches the project's existing preference (`JournalEntryStatus`, `InvoiceKind`, etc.).
- Named constraints (`uq_*`) — matches `uq_settlement_amount_positive`-style naming already in use.
- `batch_alter_table`/plain `op.create_table` migration style — matches the most recent existing migrations exactly.

## One deliberate deviation, documented in both the model and the migration
`CorrectionEvent.approved_candidate_state_id` has **no DB-level FK constraint**, despite semantically referencing `correction_event_candidate_states.id`. Reason: a genuine circular reference between two new tables (each references the other). Rather than a two-phase create-then-ALTER migration (SQLite's batch mode rebuilds the whole table per ALTER, adding complexity with no real safety benefit here), this follows the project's own existing precedent of using a plain, unconstrained reference column where a true circular FK would be disproportionate (the same spirit as the existing `source_type`/`source_id` pattern, which also carries no DB-level FK). Enforcement of this specific reference's integrity is explicitly left to the service layer, in the next phase.

## Behavior preservation — proven, not assumed
- `python3 tests/test_alembic_integration.py` — all 11 checks pass, including a synthetic "future migration" building cleanly on top of this one.
- `python3 tests/test_migration_double_run_safety.py` — passes; migration applies exactly once across repeated app-open attempts.
- `python3 tests/test_app_path_after_alembic.py` — all 7 checks pass: purchase, sale, sales return, manual JV, inventory query, and trial balance all still work and balance through the real application path after this migration is applied.
- `python3 tests/test_characterization_3b5a.py` — **8/8 PASS**, unchanged.
- `python3 tests/run_gate.py` — **Full Regression 42/42 PASS (0 failures)**, **Fuzz 200/200 PASS (0 failures)**, unchanged.
- `python3 tests/test_correction_event_schema.py` — **17/17 PASS** (new).

## What the new schema tests actually prove (not just "tables exist")
- The load-bearing constraint works: `UNIQUE(candidate_state_id, tie_group_id)` **actually rejects** (via a real `IntegrityError`, caught and verified) an attempt to give one candidate two contradictory resolutions for the same tie-group — while correctly **allowing** a second, distinct candidate to use the other resolution.
- A cycle (`el1 → el2 → el1`) in `ImpactScopeRelationship` is representable without any schema-level rejection, matching Q2.4's proven graph shape.
- `approved_candidate_state_id` is `NULL` by default and only set by an explicit later action — no candidate is structurally privileged before approval.
- A duplicate scope element and a duplicate `journal_entry_id` link are both rejected at the DB level, not left to application discipline alone.
- Two items require two separate `CorrectionEvent` rows — Item Scope is enforced by the data model's own shape, not by convention.

## Explicitly not done in this phase (per your prohibition list)
No Historical Correction detection, no impact analysis algorithm, no recalculation logic, no UI, no background job, no Period Lock. This is schema only, ready to be built upon — not yet functional as a correction engine.

## Status
PHASE 3B-5-C — Correction Event Schema Implementation: **COMPLETE**. Ready for your review before Detection/Analysis/Recalculation logic begins.
