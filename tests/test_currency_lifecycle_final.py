"""
tests/test_currency_lifecycle_final.py
==========================================
Phase 1 / بند 1 من مراجعة Bilal الأخيرة: دورة فاتورة بالعملة كاملة —
فاتورة بسعر تاريخي → تسويتان جزئيتان بسعرين مختلفين → تسوية نهائية،
مع فحص JournalLines الفعلية مباشرة (لا الاكتفاء بنجاح حسابي)، لكل من
USD وEUR بمعزل. يُثبِت:
  - realized FX الصحيح في كل تسوية على حدة
  - الرصيد يصل صفراً تماماً
  - القيود الأساسية متوازنة (debit_base = credit_base) بكل خطوة
  - القيمة التاريخية للفاتورة (invoice.exchange_rate وقيمة أسطرها) لا
    تُعاد كتابتها أبداً بسعر أي تسوية لاحقة
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, Settlement,
    JournalLine, CostMethod,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_sales_invoice
from app.services.settlements import post_receipt, get_invoice_balance_due, SettlementError

today = datetime.date.today()
results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def run_currency_scenario(currency: str, invoice_rate: D_, rate1: D_, rate2: D_, rate3: D_):
    print(f"\n== سيناريو {currency}: فاتورة @{invoice_rate} → تسويات @{rate1}/{rate2}/{rate3} ==")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    coa = create_default_chart_of_accounts(session)
    item = create_item(session, sku=f"CUR-{currency}", name_ar=f"مادة {currency}", unit="قطعة",
                        inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
                        cost_method=CostMethod.AVERAGE)
    session.commit()

    inv = Invoice(invoice_no=f"CUR-{currency}-1", kind=InvoiceKind.SALES, party_name=f"عميل {currency}",
                   invoice_date=today, currency_code=currency, exchange_rate=invoice_rate,
                   status=InvoiceStatus.DRAFT)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=D_("1"), unit_price=D_("1000"))]
    session.add(inv); session.commit()
    post_sales_invoice(session, inv, is_cash=False)
    session.commit()

    # القيمة التاريخية الأصلية — نحفظها الآن لمقارنتها بعد كل التسويات
    original_invoice_rate = inv.exchange_rate
    original_line_price = inv.lines[0].unit_price
    grand_total_foreign = D_("1000")  # 1×1000، بلا خصم/ضريبة بهذا السيناريو

    amounts = [D_("400"), D_("350"), D_("250")]  # 400+350+250=1000 بالضبط
    rates = [rate1, rate2, rate3]
    cumulative_fx = D_("0")

    for i, (amt, rate) in enumerate(zip(amounts, rates), start=1):
        balance_before = get_invoice_balance_due(session, inv)
        entry = post_receipt(session, inv, amt, today, rate, coa["cash"].id)
        session.commit()

        # --- Oracle مستقل: realized FX يدوياً بلا استدعاء أي دالة بالمحرك ---
        expected_fx = (amt * rate) - (amt * original_invoice_rate)  # موجب=ربح لعميل قبضنا أكثر
        settlement_row = session.query(Settlement).filter_by(
            invoice_id=inv.id, kind="receipt"
        ).order_by(Settlement.id.desc()).first()
        check(f"[{currency}] تسوية #{i}: fx_amount المخزَّن = Oracle يدوي بالضبط",
              settlement_row.fx_amount == expected_fx,
              f"stored={settlement_row.fx_amount} expected={expected_fx}")

        # --- فحص JournalLines الفعلية مباشرة (لا الاكتفاء بنجاح حسابي) ---
        lines = session.query(JournalLine).filter_by(entry_id=entry.id).all()
        cash_line = next(l for l in lines if l.account_id == coa["cash"].id)
        check(f"[{currency}] تسوية #{i}: سطر الصندوق raw={amt} بسعر التسوية {rate} فعلياً",
              cash_line.debit == amt and D_(str(cash_line.debit_base)) == amt * rate)
        ar_line = next(l for l in lines if l.account_id != coa["cash"].id
                        and l.account_id not in (coa["fx_gain"].id, coa["fx_loss"].id))
        check(f"[{currency}] تسوية #{i}: سطر الذمم يُقفَل بسعر الفاتورة الأصلي {original_invoice_rate} لا سعر اليوم",
              D_(str(ar_line.credit_base)) == amt * original_invoice_rate)
        if expected_fx != 0:
            fx_line = next((l for l in lines if l.account_id in (coa["fx_gain"].id, coa["fx_loss"].id)), None)
            check(f"[{currency}] تسوية #{i}: سطر فرق صرف فعلي وُلِد بالقيمة الصحيحة",
                  fx_line is not None and D_(str(fx_line.debit_base or fx_line.credit_base)) == abs(expected_fx))
        entry_balance = sum(D_(str(l.debit_base)) for l in lines) - sum(D_(str(l.credit_base)) for l in lines)
        check(f"[{currency}] تسوية #{i}: القيد متوازن تماماً بالعملة الأساسية (debit_base=credit_base)",
              entry_balance == D_("0"), f"diff={entry_balance}")

        cumulative_fx += expected_fx
        balance_after = get_invoice_balance_due(session, inv)
        check(f"[{currency}] تسوية #{i}: الرصيد المتبقي = المتبقي السابق - {amt} بالضبط",
              balance_after == balance_before - amt, f"actual={balance_after}")

    # --- الرصيد النهائي صفر تماماً ---
    final_balance = get_invoice_balance_due(session, inv)
    check(f"[{currency}] الرصيد النهائي = صفر بالضبط بعد 3 تسويات", final_balance == D_("0"))

    # --- لا إعادة حساب للقيمة التاريخية للفاتورة بسعر أي تسوية لاحقة ---
    check(f"[{currency}] invoice.exchange_rate لم يتغيّر إطلاقاً عن سعر الترحيل الأصلي",
          inv.exchange_rate == original_invoice_rate)
    check(f"[{currency}] سعر سطر الفاتورة (unit_price) لم يُعَد حسابه بأي سعر تسوية",
          inv.lines[0].unit_price == original_line_price)

    # --- محاولة تسوية رابعة (لا رصيد متبقٍ) يجب أن تُرفَض ---
    try:
        post_receipt(session, inv, D_("1"), today, rate3, coa["cash"].id)
        check(f"[{currency}] تسوية رابعة على فاتورة مُسدَّدة بالكامل رُفضت", False, "لم تُرفَض!")
    except SettlementError:
        check(f"[{currency}] تسوية رابعة على فاتورة مُسدَّدة بالكامل رُفضت بوضوح", True)

    # --- Trial Balance فعلي متوازن (بند 4 من مراجعة Bilal لإغلاق Phase 2:
    #     نفس هذا السيناريو يعمل بلا أي تغيير بعد إضافة subtype/
    #     allow_reconciliation — عملة العملية وسعر الصرف وbase debit/credit
    #     والرصيد المستحق وrealized FX أعلاه، والآن ميزان المراجعة أيضاً) ---
    from app.reports.trial_balance import get_trial_balance
    tb = get_trial_balance(session)
    check(f"[{currency}] ميزان المراجعة الفعلي متوازن تماماً بعد كل التسويات", tb.is_balanced)

    return cumulative_fx


fx_usd = run_currency_scenario("USD", D_("10"), D_("10.5"), D_("9.8"), D_("10.2"))
fx_eur = run_currency_scenario("EUR", D_("12"), D_("12.3"), D_("11.9"), D_("12.1"))

print()
print("=" * 70)
print(f"✅ دورة العملة الكاملة (USD وEUR) نجحت — {len(results)} تحقّقاً")
print(f"   صافي فرق الصرف USD المتراكم: {fx_usd} | EUR: {fx_eur}")
print("=" * 70)
