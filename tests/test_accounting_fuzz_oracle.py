"""
tests/test_accounting_fuzz_oracle.py
======================================
اختبار fuzz حقيقي: يشغّل محرك الترحيل الفعلي (post_purchase_invoice) على
سلاسل شراء عشوائية بعملات وأسعار صرف مختلفة، ويقارن النتيجة بمعادلة
oracle مستقلة تماماً — لا تستدعي app/services/posting.py ولا
app/services/item_queries.py ولا أي دالة إنتاجية، فقط Decimal يدوي.

القاعدة الصارمة التي طُلبت: oracle منفصل تماماً لتفادي تكرار مشكلة
"8/8 اختبارات نجحت رغم وجود خطأ محاسبي حقيقي" (WORKFLOW.md §30).

نطاق هذا الملف عمداً محدود بسيناريوهات الشراء فقط (بلا بيع/مرتجع/ضريبة/
حسم) لإبقاء معادلة الـoracle بسيطة ومستقلة بثقة كاملة. حالات البيع
والمرتجع والعملات المختلطة مغطاة أصلاً في test_accounting_edge_cases.py
كحالات يدوية دقيقة — هذا الملف يضيف التغطية العشوائية الواسعة، لا يستبدلها.
"""
import os, sys, datetime, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from pathlib import Path
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Account, Item, CostMethod, Invoice, InvoiceLine,
    InvoiceKind, InvoiceStatus, InventoryMovement, JournalLine,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice
from app.services.sanity_guard import AccountingSanityError
from tests.fuzz_report import FuzzReport, ScenarioResult

D_ = Decimal
today = datetime.date.today()

NUM_SCENARIOS = 200
CURRENCIES = {
    "SYP": D_("1"),
    "USD": D_("15000"),
    "EUR": D_("16500"),
}


def _fresh_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def oracle_weighted_average(
    purchases: list[tuple[Decimal, Decimal, Decimal]]
) -> tuple[Decimal, Decimal]:
    """
    purchases: (qty, unit_price_in_doc_currency, exchange_rate)
    يُعيد (متوسط التكلفة المرجّح بالعملة الأساسية, إجمالي قيمة المخزون).
    معادلة مستقلة تماماً — مكتوبة يدوياً هنا فقط، لا تستدعي أي كود إنتاجي.
    """
    total_qty = D_("0")
    total_base_cost = D_("0")
    for qty, price, rate in purchases:
        total_qty += qty
        total_base_cost += qty * price * rate
    if total_qty == 0:
        return D_("0"), D_("0")
    return total_base_cost / total_qty, total_base_cost


def random_purchase_sequence(seed: int) -> list[tuple[Decimal, Decimal, Decimal]]:
    rnd = random.Random(seed)
    n = rnd.randint(2, 6)
    seq = []
    for _ in range(n):
        currency = rnd.choice(list(CURRENCIES.keys()))
        rate = CURRENCIES[currency]
        qty = D_(str(rnd.randint(1, 500)))
        price = D_(str(rnd.randint(1, 5000)))
        seq.append((qty, price, rate, currency))
    return seq


def run_scenario(seed: int) -> ScenarioResult:
    session = _fresh_session()
    coa = create_default_chart_of_accounts(session)
    item = create_item(
        session, sku=f"FUZZ-{seed}", name_ar="مادة fuzz", unit="قطعة",
        inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
        cost_method=CostMethod.AVERAGE,
    )
    session.commit()

    sequence = random_purchase_sequence(seed)
    sanity_errors = 0
    total_debit_base = D_("0")
    total_credit_base = D_("0")

    for i, (qty, price, rate, currency) in enumerate(sequence):
        inv = Invoice(
            invoice_no=f"FZ-{seed}-{i}", kind=InvoiceKind.PURCHASE, party_name="مورد fuzz",
            invoice_date=today - datetime.timedelta(days=len(sequence) - i),
            currency_code=currency, exchange_rate=rate, status=InvoiceStatus.DRAFT,
        )
        inv.lines = [InvoiceLine(item_id=item.id, quantity=qty, unit_price=price)]
        session.add(inv)
        session.commit()
        try:
            entry = post_purchase_invoice(session, inv, is_cash=True)
            session.commit()
        except AccountingSanityError:
            sanity_errors += 1
            session.rollback()
            continue

        for line in entry.lines:
            total_debit_base += D_(line.debit_base)
            total_credit_base += D_(line.credit_base)

    # --- oracle مستقل ---
    oracle_purchases = [(qty, price, rate) for qty, price, rate, _ in sequence]
    expected_avg_cost, expected_inventory_value = oracle_weighted_average(oracle_purchases)

    # --- قراءة النتائج الفعلية مباشرة من البيانات المخزَّنة (لا عبر get_item_stock_summary) ---
    movements = session.execute(
        select(InventoryMovement).where(InventoryMovement.item_id == item.id)
    ).scalars().all()
    actual_total_qty = sum(D_(m.quantity) for m in movements) or D_("1")
    actual_total_value = sum(D_(m.quantity) * D_(m.unit_cost) for m in movements)
    actual_avg_cost = actual_total_value / actual_total_qty if actual_total_qty else D_("0")

    inventory_account_id = coa["inventory"].id
    actual_inventory_balance = session.execute(
        select(JournalLine).where(JournalLine.account_id == inventory_account_id)
    ).scalars().all()
    actual_inventory_debit = sum(D_(l.debit_base) for l in actual_inventory_balance)

    session.close()

    return ScenarioResult(
        seed=seed,
        num_operations=len(sequence),
        currencies_used=list({c for _, _, _, c in sequence}),
        exchange_rates={c: CURRENCIES[c] for c in {c for _, _, _, c in sequence}},
        expected_balances={"inventory": expected_inventory_value},
        actual_balances={"inventory": actual_inventory_debit},
        expected_avg_cost={f"item-{seed}": expected_avg_cost},
        actual_avg_cost={f"item-{seed}": actual_avg_cost},
        total_debit_base=total_debit_base,
        total_credit_base=total_credit_base,
        sanity_errors=sanity_errors,
    )


def test_fuzz_purchase_weighted_average_matches_independent_oracle():
    report = FuzzReport()
    for seed in range(NUM_SCENARIOS):
        report.scenarios.append(run_scenario(seed))

    # يُستكمل يدوياً بربط نتيجة regression الكامل (test_accounting_edge_cases.py +
    # test_e2e_scenario.py) قبل اعتبار البوابة صالحة — لا تُترك None بالتشغيل الفعلي.
    report.regression_suite_passed = None

    out_dir = Path(__file__).resolve().parent.parent / "reports_out"
    out_dir.mkdir(exist_ok=True)
    report.save(out_dir / "fuzz_report")

    failed = [s for s in report.scenarios if not s.passes()]
    assert not failed, (
        f"{len(failed)} سيناريو فشل من أصل {NUM_SCENARIOS} — "
        f"راجع reports_out/fuzz_report.md للتفاصيل"
    )


if __name__ == "__main__":
    test_fuzz_purchase_weighted_average_matches_independent_oracle()
    print("✅ نجح fuzz test — راجع reports_out/fuzz_report.md")
