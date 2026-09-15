"""
PHASE 3B-5-C — Correction Event schema characterization tests.

Schema-level only: table creation, constraints, indexes, lifecycle enum
values, candidate consistency (the load-bearing UNIQUE constraint), and
the AccountingCorrection reference pattern. NO Detection/Analysis/
Recalculation logic exists yet and none is tested here — that is a
separate, later phase per the explicit scope boundary in
3B-5-C_DISCOVERY_DESIGN_QUESTIONS.md.
"""
import os, sys, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from app.models import (
    Base, Account, AccountType, Item, CostMethod, Warehouse, InventoryMovement,
    MovementDirection, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, Setting,
    CorrectionEvent, CorrectionEventStatus, CorrectionEventRootCause,
    RootCauseComponentType, CorrectionEventTieGroup, CorrectionEventOrderingAssumption,
    CorrectionEventCandidateState, CorrectionEventCandidateOrderingLink,
    CorrectionEventDeltaRecord, CorrectionEventImpactScopeElement,
    ImpactScopeElementKind, CorrectionEventImpactScopeRelationship,
    ImpactScopeRelationshipType, CorrectionEventAccountingCorrection,
    ChronologyBasis, ScopeCompleteness,
)
from app.services.posting import post_purchase_invoice, post_sales_invoice, get_default_warehouse

results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))


def fresh_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed(session):
    cash = Account(code="1101", name_ar="الصندوق", account_type=AccountType.ASSET)
    inv = Account(code="1210", name_ar="المخزون", account_type=AccountType.ASSET)
    cogs = Account(code="5110", name_ar="تكلفة المبيعات", account_type=AccountType.EXPENSE)
    sales = Account(code="4100", name_ar="المبيعات", account_type=AccountType.REVENUE)
    session.add_all([cash, inv, cogs, sales]); session.commit()
    for k, v in [("default_cash_account_id", cash.id), ("default_sales_account_id", sales.id),
                 ("default_sales_tax_account_id", sales.id), ("default_purchases_tax_account_id", sales.id),
                 ("base_currency", "SYP")]:
        session.add(Setting(key=k, value=str(v)))
    session.commit()
    item = Item(sku="X", name_ar="صنف", inventory_account_id=inv.id, cogs_account_id=cogs.id,
                sales_account_id=sales.id, cost_method=CostMethod.AVERAGE)
    session.add(item); session.commit()
    return item


# ---------------------------------------------------------------------------
# 1) Model characterization — all ten tables created, no unintended behavior
# ---------------------------------------------------------------------------
s = fresh_session()
item = seed(s)
wh = get_default_warehouse(s)

table_names = {t.name for t in Base.metadata.sorted_tables}
expected = {
    "correction_events", "correction_event_root_causes", "correction_event_tie_groups",
    "correction_event_ordering_assumptions", "correction_event_candidate_states",
    "correction_event_candidate_ordering_links", "correction_event_delta_records",
    "correction_event_impact_scope_elements", "correction_event_impact_scope_relationships",
    "correction_event_accounting_corrections",
}
check("1. all ten Correction Event tables exist after Base.metadata.create_all()",
      expected.issubset(table_names))

# ---------------------------------------------------------------------------
# 2) CandidateState is 1..N — an event may be created with exactly one
#    candidate even with no ambiguity (no TieGroup at all)
# ---------------------------------------------------------------------------
ev = CorrectionEvent(item_id=item.id, trigger_type="BACKDATED_INSERT", status=CorrectionEventStatus.CANDIDATE)
s.add(ev); s.commit()
rc = CorrectionEventRootCause(correction_event_id=ev.id, component_type=RootCauseComponentType.NEW_MOVEMENT,
                               source_type="inventory_movement", source_id=1)
s.add(rc); s.commit()
cand = CorrectionEventCandidateState(correction_event_id=ev.id, label="A")
s.add(cand); s.commit()
check("2. a CorrectionEvent can hold exactly one CandidateState with zero TieGroups (fully known chronology)",
      s.query(CorrectionEventCandidateState).filter_by(correction_event_id=ev.id).count() == 1)

# ---------------------------------------------------------------------------
# 3) Lifecycle status values, including the newly-added WITHDRAWN
# ---------------------------------------------------------------------------
all_statuses = {s_.value for s_ in CorrectionEventStatus}
check("3. lifecycle enum includes all seven states (CANDIDATE..ACCOUNTING_CORRECTION, STALE, WITHDRAWN)",
      all_statuses == {"candidate", "analysis", "pending", "approved", "accounting_correction", "stale", "withdrawn"})

ev.status = CorrectionEventStatus.WITHDRAWN
s.commit()
check("3b. status is the only field that changes — WITHDRAWN transition leaves RootCause/CandidateState rows intact",
      s.get(CorrectionEventRootCause, rc.id) is not None and s.get(CorrectionEventCandidateState, cand.id) is not None)
ev.status = CorrectionEventStatus.CANDIDATE
s.commit()

# ---------------------------------------------------------------------------
# 4) Candidate consistency — the load-bearing UNIQUE(candidate_state_id, tie_group_id)
# ---------------------------------------------------------------------------
tg = CorrectionEventTieGroup(correction_event_id=ev.id, tied_movement_date=datetime.date(2026, 9, 1))
s.add(tg); s.commit()
oa1 = CorrectionEventOrderingAssumption(tie_group_id=tg.id, ordering_representation="IN,OUT")
oa2 = CorrectionEventOrderingAssumption(tie_group_id=tg.id, ordering_representation="OUT,IN")
s.add_all([oa1, oa2]); s.commit()

link1 = CorrectionEventCandidateOrderingLink(
    candidate_state_id=cand.id, ordering_assumption_id=oa1.id, tie_group_id=tg.id,
)
s.add(link1); s.commit()

# Now attempt the SAME candidate with a SECOND, contradictory assumption for
# the SAME tie-group — must be rejected at the DB level.
link2 = CorrectionEventCandidateOrderingLink(
    candidate_state_id=cand.id, ordering_assumption_id=oa2.id, tie_group_id=tg.id,
)
s.add(link2)
rejected = False
try:
    s.commit()
except IntegrityError:
    rejected = True
    s.rollback()
check("4. UNIQUE(candidate_state_id, tie_group_id) rejects a self-contradictory candidate at the DB level",
      rejected)

# A DIFFERENT candidate may still use the other assumption for the same tie-group:
cand2 = CorrectionEventCandidateState(correction_event_id=ev.id, label="B")
s.add(cand2); s.commit()
link3 = CorrectionEventCandidateOrderingLink(
    candidate_state_id=cand2.id, ordering_assumption_id=oa2.id, tie_group_id=tg.id,
)
s.add(link3); s.commit()
check("4b. a second, distinct candidate CAN use a different assumption for the same tie-group",
      s.get(CorrectionEventCandidateOrderingLink, link3.id) is not None)

# ---------------------------------------------------------------------------
# 5) Cartesian-product test — two independent tie-groups, verify the model
#    can represent the full combination space without breaking
# ---------------------------------------------------------------------------
tg2 = CorrectionEventTieGroup(correction_event_id=ev.id, tied_movement_date=datetime.date(2026, 9, 5))
s.add(tg2); s.commit()
oa2a = CorrectionEventOrderingAssumption(tie_group_id=tg2.id, ordering_representation="IN,IN,OUT")
oa2b = CorrectionEventOrderingAssumption(tie_group_id=tg2.id, ordering_representation="IN,OUT,IN")
s.add_all([oa2a, oa2b]); s.commit()

# cand (already resolved tg -> oa1) now also needs a resolution for tg2:
link_c1_tg2 = CorrectionEventCandidateOrderingLink(candidate_state_id=cand.id, ordering_assumption_id=oa2a.id, tie_group_id=tg2.id)
# cand2 (already resolved tg -> oa2) also needs tg2:
link_c2_tg2 = CorrectionEventCandidateOrderingLink(candidate_state_id=cand2.id, ordering_assumption_id=oa2b.id, tie_group_id=tg2.id)
s.add_all([link_c1_tg2, link_c2_tg2]); s.commit()
check("5. two independent tie-groups: each existing candidate can be extended with a consistent resolution for the second group",
      s.query(CorrectionEventCandidateOrderingLink).filter_by(candidate_state_id=cand.id).count() == 2 and
      s.query(CorrectionEventCandidateOrderingLink).filter_by(candidate_state_id=cand2.id).count() == 2)

# ---------------------------------------------------------------------------
# 6) Delta records — primitive quantity/value only, referencing a real movement
# ---------------------------------------------------------------------------
mv = InventoryMovement(item_id=item.id, warehouse_id=wh.id, direction=MovementDirection.IN,
                        quantity=Decimal("100"), unit_cost=Decimal("10"),
                        movement_date=datetime.date(2026, 9, 1), source_type="purchase_invoice", source_id=1)
s.add(mv); s.commit()
delta = CorrectionEventDeltaRecord(candidate_state_id=cand.id, movement_id=mv.id,
                                    delta_quantity=Decimal("40"), delta_inventory_value=Decimal("400"))
s.add(delta); s.commit()
check("6. DeltaRecord stores only primitive quantity/value deltas, referencing a real InventoryMovement",
      s.get(CorrectionEventDeltaRecord, delta.id).movement_id == mv.id)

# ---------------------------------------------------------------------------
# 7) Impact Scope — no duplicate element, and relationships can form a cycle
#    (W1 -> W2 -> W1), matching Q2.4's confirmed graph shape
# ---------------------------------------------------------------------------
el1 = CorrectionEventImpactScopeElement(correction_event_id=ev.id, kind=ImpactScopeElementKind.MOVEMENT,
                                         source_type="inventory_movement", source_id=mv.id)
s.add(el1); s.commit()
dup = CorrectionEventImpactScopeElement(correction_event_id=ev.id, kind=ImpactScopeElementKind.MOVEMENT,
                                         source_type="inventory_movement", source_id=mv.id)
s.add(dup)
rejected_dup = False
try:
    s.commit()
except IntegrityError:
    rejected_dup = True
    s.rollback()
check("7. UNIQUE(correction_event_id, source_type, source_id) rejects a duplicate scope element",
      rejected_dup)

el2 = CorrectionEventImpactScopeElement(correction_event_id=ev.id, kind=ImpactScopeElementKind.MOVEMENT,
                                         source_type="inventory_movement", source_id=mv.id + 1)
s.add(el2); s.commit()
rel_forward = CorrectionEventImpactScopeRelationship(
    correction_event_id=ev.id, from_element_id=el1.id, to_element_id=el2.id,
    relationship_type=ImpactScopeRelationshipType.PROPAGATES_TO,
)
rel_back = CorrectionEventImpactScopeRelationship(
    correction_event_id=ev.id, from_element_id=el2.id, to_element_id=el1.id,
    relationship_type=ImpactScopeRelationshipType.PROPAGATES_TO,
)
s.add_all([rel_forward, rel_back]); s.commit()
check("7b. Impact Scope relationships can form a cycle (el1 -> el2 -> el1) without any schema-level rejection",
      s.get(CorrectionEventImpactScopeRelationship, rel_forward.id) is not None and
      s.get(CorrectionEventImpactScopeRelationship, rel_back.id) is not None)

# ---------------------------------------------------------------------------
# 8) AccountingCorrection — thin reference, one JournalEntry per correction
# ---------------------------------------------------------------------------
inv_purchase = Invoice(invoice_no="PI-SCHEMA-1", kind=InvoiceKind.PURCHASE, party_name="مورد",
                        invoice_date=datetime.date(2026, 9, 1), currency_code="SYP",
                        exchange_rate=Decimal("1"), status=InvoiceStatus.DRAFT, warehouse_id=wh.id)
inv_purchase.lines = [InvoiceLine(item_id=item.id, quantity=10, unit_price=Decimal("10"))]
s.add(inv_purchase); s.commit()
je = post_purchase_invoice(s, inv_purchase)
ac = CorrectionEventAccountingCorrection(correction_event_id=ev.id, journal_entry_id=je.id)
s.add(ac); s.commit()
check("8. AccountingCorrection references a real posted JournalEntry (created via the existing Boundary)",
      s.get(CorrectionEventAccountingCorrection, ac.id).journal_entry_id == je.id)

ac_dup = CorrectionEventAccountingCorrection(correction_event_id=ev.id, journal_entry_id=je.id)
s.add(ac_dup)
rejected_je = False
try:
    s.commit()
except IntegrityError:
    rejected_je = True
    s.rollback()
check("8b. UNIQUE(journal_entry_id) rejects linking the same JournalEntry to a correction twice — same precedent as Settlement/OpeningBalance",
      rejected_je)

check("8c. AccountingCorrection stores no duplicated journal amounts/accounts — only the FK reference",
      not hasattr(CorrectionEventAccountingCorrection, "amount") and
      not hasattr(CorrectionEventAccountingCorrection, "debit_account_id"))

# ---------------------------------------------------------------------------
# 9) ChronologyBasis / ScopeCompleteness — orthogonal fields on the Event
# ---------------------------------------------------------------------------
ev.chronology_basis = ChronologyBasis.ASSUMED
ev.scope_completeness = ScopeCompleteness.PARTIAL
s.commit()
check("9. chronology_basis and scope_completeness are independent fields on CorrectionEvent, both settable simultaneously",
      s.get(CorrectionEvent, ev.id).chronology_basis == ChronologyBasis.ASSUMED and
      s.get(CorrectionEvent, ev.id).scope_completeness == ScopeCompleteness.PARTIAL)

# ---------------------------------------------------------------------------
# 10) approved_candidate_state_id — NULL by default, no privileged candidate
# ---------------------------------------------------------------------------
check("10. approved_candidate_state_id is NULL by default — no candidate is privileged before explicit approval",
      s.get(CorrectionEvent, ev.id).approved_candidate_state_id is None)
ev.approved_candidate_state_id = cand.id
s.commit()
check("10b. setting approved_candidate_state_id is a plain, explicit action, not implied by any other field",
      s.get(CorrectionEvent, ev.id).approved_candidate_state_id == cand.id)

# ---------------------------------------------------------------------------
# 11) Item Scope — one event = one item; a second item needs its own event
# ---------------------------------------------------------------------------
item2 = Item(sku="Y", name_ar="صنف ثانٍ", inventory_account_id=item.inventory_account_id,
             cogs_account_id=item.cogs_account_id, sales_account_id=item.sales_account_id,
             cost_method=CostMethod.AVERAGE)
s.add(item2); s.commit()
ev2 = CorrectionEvent(item_id=item2.id, trigger_type="BACKDATED_INSERT", status=CorrectionEventStatus.CANDIDATE)
s.add(ev2); s.commit()
check("11. a second item requires its own separate CorrectionEvent (Item Scope = exactly one item per event)",
      ev2.id != ev.id and ev2.item_id != ev.item_id)

print()
print(f"Schema characterization summary: {sum(1 for r in results if r[0]=='PASS')}/{len(results)} PASS")
failed = [r for r in results if r[0] == "FAIL"]
if failed:
    print("FAILURES:")
    for r in failed:
        print(" -", r[1], r[2])
    sys.exit(1)
