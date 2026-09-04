"""
اختبارات محاسبية مركزة — طبقة إضافية بعد اختبار End-to-End الشامل، بالضبط
الـ15 حالة التي طلب صديق المستخدم تغطيتها قبل الانتقال لأي شاشة جديدة.

كل حالة معزولة بقاعدة بيانات منفصلة (in-memory) — لا تشارك حالة مع غيرها،
حتى يسهل تحديد أي حالة فشلت بالضبط بلا تداخل. القاعدة المعمارية التي طلب
تثبيتها ("كل قيمة مالية يجب أن يكون معروفاً هل هي بالعملة الأصلية أم
الأساسية") هي المعيار الذي تُقاس عليه كل حالة أدناه — كل تحقق يقارن رقماً
محسوباً يدوياً بعملة معروفة صراحة، لا افتراضاً ضمنياً.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Account, AccountType, Item, CostMethod, Invoice, InvoiceLine,
    InvoiceKind, InvoiceStatus, JournalEntry, JournalEntryStatus, Setting,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.opening_balances import post_opening_inventory, OpeningInventoryLineInput
from app.services.item_queries import get_item_stock_summary
from app.services.posting import post_purchase_invoice, post_sales_invoice, get_default_warehouse, PostingError
from app.services.returns import post_sales_return, post_purchase_return
from app.services.journal_edit import add_manual_line, post_manual_entry, reverse_manual_entry
from app.services.invoice_edit import ensure_editable as ensure_invoice_editable, EditNotAllowedError
from app.services.invoice_validation import InvoiceValidationError
from app.reports.ledger import get_account_statement
from app.reports.trial_balance import get_trial_balance
from app.services.money import money, D

D_ = Decimal
today = datetime.date.today()


def _fresh_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _setup_basic(session):
    """شجرة قياسية + مادة واحدة جاهزة بحساباتها الافتراضية + رأس مال."""
    coa = create_default_chart_of_accounts(session)
    item = create_item(
        session, sku="X-001", name_ar="مادة اختبار", unit="قطعة",
        inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
        cost_method=CostMethod.AVERAGE,
    )
    session.commit()
    equity = session.query(Account).filter_by(code="3101").first()
    capital = JournalEntry(entry_date=today - datetime.timedelta(days=60), ref_no="JV-CAP",
                            description="رأس مال", source_type="opening_balance",
                            currency_code="SYP", exchange_rate=1, status=JournalEntryStatus.DRAFT)
    session.add(capital)
    session.flush()
    add_manual_line(session, capital, coa["cash"].id, debit=D_("1000000000"), exchange_rate=1)
    add_manual_line(session, capital, equity.id, credit=D_("1000000000"), exchange_rate=1)
    post_manual_entry(session, capital)
    session.commit()
    return coa, item


results = []


def check(name, condition, detail=""):
    status = "✅ OK" if condition else "❌ FAIL"
    print(f"{status}  {name}" + (f" — {detail}" if detail and not condition else ""))
    results.append((name, condition))
    if not condition:
        raise SystemExit(f"FAILED: {name} — {detail}")


# ============================================================================
print("=" * 70); print("1. شراء أجنبي ثم شراء محلي لنفس المادة (عكس ترتيب E2E)")
print("=" * 70)
s = _fresh_session()
coa, item = _setup_basic(s)
d1_, d2_ = today - datetime.timedelta(days=10), today - datetime.timedelta(days=5)

pur_usd = Invoice(invoice_no="P1", kind=InvoiceKind.PURCHASE, party_name="مورد1",
                   invoice_date=d1_, currency_code="USD", exchange_rate=D_("15000"), status=InvoiceStatus.DRAFT)
pur_usd.lines = [InvoiceLine(item_id=item.id, quantity=10, unit_price=D_("20"))]  # 20 USD/وحدة → 300,000 SYP/وحدة
s.add(pur_usd); s.commit()
post_purchase_invoice(s, pur_usd, is_cash=True); s.commit()

pur_syp = Invoice(invoice_no="P2", kind=InvoiceKind.PURCHASE, party_name="مورد2",
                   invoice_date=d2_, currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
pur_syp.lines = [InvoiceLine(item_id=item.id, quantity=10, unit_price=D_("250000"))]
s.add(pur_syp); s.commit()
post_purchase_invoice(s, pur_syp, is_cash=True); s.commit()

summary = get_item_stock_summary(s, item.id)
expected_avg = (D_("10") * D_("300000") + D_("10") * D_("250000")) / D_("20")
check("متوسط تكلفة صحيح (أجنبي أولاً ثم محلي)", abs(summary.average_cost - expected_avg) < D_("0.01"),
      f"فعلي={summary.average_cost}, متوقَّع={expected_avg}")

# ============================================================================
print(); print("=" * 70); print("2. بيع بعد تغيّر متوسط التكلفة (يجب أن يستخدم COGS آخر متوسط وقت البيع تحديداً)")
print("=" * 70)
s = _fresh_session()
coa, item = _setup_basic(s)
s.add(Setting(key="base_currency", value="SYP"))
equity2 = s.query(Account).filter_by(code="3101").first()
s.add(Setting(key="opening_balance_clearing_account_id", value=str(equity2.id)))
s.commit()
default_wh2 = get_default_warehouse(s)
post_opening_inventory(s, [OpeningInventoryLineInput(
    item_id=item.id, warehouse_id=default_wh2.id, quantity=D_("100"), unit_cost_foreign=D_("1000"))],
    today - datetime.timedelta(days=20))
s.commit()
pur = Invoice(invoice_no="P1", kind=InvoiceKind.PURCHASE, party_name="مورد",
              invoice_date=today - datetime.timedelta(days=10), currency_code="SYP", exchange_rate=1, status=InvoiceStatus.DRAFT)
pur.lines = [InvoiceLine(item_id=item.id, quantity=100, unit_price=D_("2000"))]
s.add(pur); s.commit()
post_purchase_invoice(s, pur, is_cash=True); s.commit()
avg_before_sale = get_item_stock_summary(s, item.id).average_cost  # (100*1000+100*2000)/200 = 1500

sale = Invoice(invoice_no="S1", kind=InvoiceKind.SALES, party_name="عميل",
               invoice_date=today - datetime.timedelta(days=5), currency_code="SYP", exchange_rate=1, status=InvoiceStatus.DRAFT)
sale.lines = [InvoiceLine(item_id=item.id, quantity=20, unit_price=D_("3000"))]
s.add(sale); s.commit()
sale_entry = post_sales_invoice(s, sale, is_cash=True); s.commit()
cogs_line = next(l for l in sale_entry.lines if l.account_id == coa["cogs"].id)
expected_cogs = money(D_("20") * avg_before_sale)
check("COGS = الكمية × متوسط التكلفة وقت البيع تحديداً", cogs_line.debit == expected_cogs,
      f"فعلي={cogs_line.debit}, متوقَّع={expected_cogs} (متوسط={avg_before_sale})")

# ============================================================================
print(); print("=" * 70); print("3. مرتجع بيع بعد تغيّر متوسط التكلفة (يجب أن يقرأ كلفة البيع الأصلية، لا المتوسط الحالي)")
print("=" * 70)
# نفس حالة #2، ثم شراء جديد يغيّر المتوسط، ثم مرتجع على البيع الأصلي
pur2 = Invoice(invoice_no="P2", kind=InvoiceKind.PURCHASE, party_name="مورد",
               invoice_date=today - datetime.timedelta(days=4), currency_code="SYP", exchange_rate=1, status=InvoiceStatus.DRAFT)
pur2.lines = [InvoiceLine(item_id=item.id, quantity=50, unit_price=D_("5000"))]  # يرفع المتوسط كثيراً
s.add(pur2); s.commit()
post_purchase_invoice(s, pur2, is_cash=True); s.commit()
avg_after_pur2 = get_item_stock_summary(s, item.id).average_cost
check("المتوسط تغيّر فعلاً بعد الشراء الثاني (شرط مسبق للاختبار)", avg_after_pur2 != avg_before_sale)

sret = Invoice(invoice_no="SR1", kind=InvoiceKind.SALES_RETURN, party_name="عميل",
               invoice_date=today - datetime.timedelta(days=1), currency_code="SYP", exchange_rate=1,
               status=InvoiceStatus.DRAFT, original_invoice_id=sale.id)
sret.lines = [InvoiceLine(item_id=item.id, quantity=5, unit_price=D_("3000"))]
s.add(sret); s.commit()
sret_entry = post_sales_return(s, sret, is_cash=True); s.commit()
sret_cogs_line = next(l for l in sret_entry.lines if l.account_id == coa["cogs"].id)
expected_return_cost = money(D_("5") * avg_before_sale)  # كلفة وقت البيع الأصلي، لا المتوسط الحالي (أعلى بكثير)
check("مرتجع البيع يقرأ كلفة البيع الأصلية بالضبط، لا المتوسط الحالي الأعلى",
      sret_cogs_line.credit == expected_return_cost,
      f"فعلي={sret_cogs_line.credit}, متوقَّع={expected_return_cost} (المتوسط الحالي الخاطئ كان سيعطي {money(D_('5')*avg_after_pur2)})")

# ============================================================================
print(); print("=" * 70); print("4. و5. مرتجع شراء بعد تغيّر سعر الصرف — شراء USD ثم إرجاعه بعد تغيّر سعر الدولار")
print("=" * 70)
s = _fresh_session()
coa, item = _setup_basic(s)
rate_at_purchase = D_("15000")
pur_usd2 = Invoice(invoice_no="P1", kind=InvoiceKind.PURCHASE, party_name="مورد أجنبي",
                    invoice_date=today - datetime.timedelta(days=10), currency_code="USD",
                    exchange_rate=rate_at_purchase, status=InvoiceStatus.DRAFT)
pur_usd2.lines = [InvoiceLine(item_id=item.id, quantity=20, unit_price=D_("10"))]
s.add(pur_usd2); s.commit()
post_purchase_invoice(s, pur_usd2, is_cash=False); s.commit()

# سعر الصرف "اليوم" ارتفع كثيراً (لأي فاتورة جديدة)، لكن هذا لا يجب أن يمسّ المرتجع المرتبط
rate_today = D_("18000")
pret = Invoice(invoice_no="PR1", kind=InvoiceKind.PURCHASE_RETURN, party_name="مورد أجنبي",
               invoice_date=today - datetime.timedelta(days=1), currency_code="USD",
               exchange_rate=rate_today,  # عمداً سعر مختلف بالمرتجع نفسه — يجب ألا يُستخدَم لتقييم البضاعة
               status=InvoiceStatus.DRAFT, original_invoice_id=pur_usd2.id)
pret.lines = [InvoiceLine(item_id=item.id, quantity=8, unit_price=D_("10"))]
s.add(pret); s.commit()
pret_entry = post_purchase_return(s, pret, is_cash=False); s.commit()
inv_line = next(l for l in pret_entry.lines if l.account_id == coa["inventory"].id)
expected_return_value = money(D_("8") * D_("10") * rate_at_purchase)  # بسعر الشراء الأصلي 15000، لا اليوم 18000
# **حرج**: نتحقق من debit_base/credit_base (الحقول الفعلية المُستخدَمة بكل
# تقرير مالي)، لا debit/credit الخام — فرق جوهري انكشف بهذا بالذات (راجع WORKFLOW.md §30)
check("مرتجع الشراء يقيّم البضاعة بسعر صرف الشراء الأصلي بالـbase فعلياً، لا سعر اليوم رغم اختلافه بالمرتجع نفسه",
      inv_line.credit_base == expected_return_value,
      f"فعلي={inv_line.credit_base}, متوقَّع={expected_return_value} (لو استُخدم سعر اليوم خطأً: {money(D_('8')*D_('10')*rate_today)})")
check("سطر المخزون بالمرتجع: debit_base == debit (لا تحويل إضافي — القيمة أساسية أصلاً)",
      inv_line.credit == inv_line.credit_base, f"credit={inv_line.credit}, credit_base={inv_line.credit_base}")
check("قيد المرتجع متوازن فعلياً بالـbase", pret_entry.is_balanced())

# ============================================================================
print(); print("=" * 70)
print("4ب. فاتورة بيع بعملة أجنبية — COGS/المخزون يجب ألا يتحوّلا مرتين (الفحص الحاسم بعد إصلاح §30)")
print("=" * 70)
sale_fx = Invoice(invoice_no="SFX1", kind=InvoiceKind.SALES, party_name="عميل أجنبي",
                   invoice_date=today - datetime.timedelta(days=1), currency_code="EUR",
                   exchange_rate=D_("16300"), status=InvoiceStatus.DRAFT)
sale_fx.lines = [InvoiceLine(item_id=item.id, quantity=3, unit_price=D_("50"))]
s.add(sale_fx); s.commit()
avg_cost_now = get_item_stock_summary(s, item.id).average_cost
sale_fx_entry = post_sales_invoice(s, sale_fx, is_cash=True); s.commit()
cogs_line_fx = next(l for l in sale_fx_entry.lines if l.account_id == coa["cogs"].id)
inv_line_fx = next(l for l in sale_fx_entry.lines if l.account_id == coa["inventory"].id)
expected_cogs_fx = money(D_("3") * avg_cost_now)
check("فاتورة بيع EUR: COGS بالـbase صحيح تماماً (لا يتضاعف بسعر الصرف)",
      cogs_line_fx.debit_base == expected_cogs_fx,
      f"فعلي={cogs_line_fx.debit_base}, متوقَّع={expected_cogs_fx} "
      f"(لو تضاعف بالخطأ بسعر 16300: {money(expected_cogs_fx * D_('16300'))})")
check("سطر COGS: debit_base == debit (قيمة أساسية أصلاً، لا علاقة لها بعملة الفاتورة)",
      cogs_line_fx.debit == cogs_line_fx.debit_base)
check("سطر المخزون بفاتورة EUR: credit_base صحيح بلا تضاعف", inv_line_fx.credit_base == expected_cogs_fx)
check("قيد البيع بعملة EUR متوازن فعلياً بالـbase", sale_fx_entry.is_balanced())

# ============================================================================
print(); print("=" * 70); print("6. و7. عميل/مورد بعملة أجنبية وتسوية لاحقة بسعر مختلف — ربح وخسارة صرف محقَّقة")
print("=" * 70)
s = _fresh_session()
coa, item = _setup_basic(s)
from app.services.parties import get_or_create_party_account
supplier = get_or_create_party_account(s, "مورد تسوية", is_customer=False)
s.commit()

# التزام 100 USD @ 15000 = 1,500,000
add_line_entry = JournalEntry(entry_date=today - datetime.timedelta(days=10), ref_no="JV-D1",
                               description="التزام بالدولار", source_type="manual",
                               currency_code="USD", exchange_rate=D_("15000"), status=JournalEntryStatus.DRAFT)
s.add(add_line_entry); s.flush()
add_manual_line(s, add_line_entry, coa["inventory"].id, debit=D_("100"), exchange_rate=D_("15000"))
add_manual_line(s, add_line_entry, supplier.id, credit=D_("100"), exchange_rate=D_("15000"))
post_manual_entry(s, add_line_entry); s.commit()

# تسوية بسعر أعلى (15500) = خسارة صرف 500×100=50,000... دعنا نحسب بدقة: الفرق للوحدة = 500، × 100 = 50,000
settle_loss_rate = D_("15500")
settle_loss_base = money(D_("100") * settle_loss_rate)
booking_base = money(D_("100") * D_("15000"))
loss = settle_loss_base - booking_base
settle_entry = JournalEntry(entry_date=today - datetime.timedelta(days=5), ref_no="JV-D2",
                             description="تسوية بخسارة صرف", source_type="manual",
                             currency_code="SYP", exchange_rate=1, status=JournalEntryStatus.DRAFT)
s.add(settle_entry); s.flush()
add_manual_line(s, settle_entry, supplier.id, debit=booking_base, exchange_rate=1)
add_manual_line(s, settle_entry, coa["cogs"].id, debit=loss, exchange_rate=1)  # نستخدم أي حساب مصروف متاح للاختبار
add_manual_line(s, settle_entry, coa["cash"].id, credit=settle_loss_base, exchange_rate=1)
post_manual_entry(s, settle_entry); s.commit()

supplier_balance = get_account_statement(s, supplier.id, None, today).closing_balance
check("خسارة صرف: الالتزام أُطفئ بالكامل (رصيد صفر) بعد التسوية بسعر أعلى", supplier_balance == 0, str(supplier_balance))

# مورد آخر منفصل لاختبار الربح (سعر تسوية أقل من سعر الحجز)
supplier2 = get_or_create_party_account(s, "مورد تسوية بربح", is_customer=False)
s.commit()
entry_g1 = JournalEntry(entry_date=today - datetime.timedelta(days=10), ref_no="JV-G1",
                         description="التزام بالدولار (لربح)", source_type="manual",
                         currency_code="USD", exchange_rate=D_("15000"), status=JournalEntryStatus.DRAFT)
s.add(entry_g1); s.flush()
add_manual_line(s, entry_g1, coa["inventory"].id, debit=D_("100"), exchange_rate=D_("15000"))
add_manual_line(s, entry_g1, supplier2.id, credit=D_("100"), exchange_rate=D_("15000"))
post_manual_entry(s, entry_g1); s.commit()

settle_gain_rate = D_("14500")  # أقل من سعر الحجز → ربح صرف
settle_gain_base = money(D_("100") * settle_gain_rate)
gain = booking_base - settle_gain_base
entry_g2 = JournalEntry(entry_date=today - datetime.timedelta(days=5), ref_no="JV-G2",
                         description="تسوية بربح صرف", source_type="manual",
                         currency_code="SYP", exchange_rate=1, status=JournalEntryStatus.DRAFT)
s.add(entry_g2); s.flush()
add_manual_line(s, entry_g2, supplier2.id, debit=booking_base, exchange_rate=1)
add_manual_line(s, entry_g2, coa["cogs"].id, credit=gain, exchange_rate=1)  # دائن = تخفيض مصروف/ربح
add_manual_line(s, entry_g2, coa["cash"].id, credit=settle_gain_base, exchange_rate=1)
post_manual_entry(s, entry_g2); s.commit()
supplier2_balance = get_account_statement(s, supplier2.id, None, today).closing_balance
check("ربح صرف: الالتزام أُطفئ بالكامل (رصيد صفر) بعد التسوية بسعر أقل", supplier2_balance == 0, str(supplier2_balance))

# ============================================================================
print(); print("=" * 70); print("8. فرق تقريب صغير جداً — يجب أن يُرفَض بوضوح لا أن يُقبَل بصمت أو يُنهار")
print("=" * 70)
s = _fresh_session()
coa, item = _setup_basic(s)
tiny_mismatch = JournalEntry(entry_date=today, ref_no="JV-TINY", description="فرق تقريب متعمَّد",
                              source_type="manual", currency_code="SYP", exchange_rate=1, status=JournalEntryStatus.DRAFT)
s.add(tiny_mismatch); s.flush()
add_manual_line(s, tiny_mismatch, coa["cash"].id, debit=D_("100.00"), exchange_rate=1)
add_manual_line(s, tiny_mismatch, coa["sales"].id, credit=D_("100.01"), exchange_rate=1)  # فرق 0.01 فقط
try:
    post_manual_entry(s, tiny_mismatch)
    check("فرق تقريب 0.01 يُرفَض عند الترحيل (لا تسامح صامت بأي مبلغ)", False, "رُحِّل بلا رفض!")
except Exception as e:
    check("فرق تقريب 0.01 يُرفَض بوضوح عند الترحيل", "غير متوازن" in str(e), str(e))

# ============================================================================
print(); print("=" * 70); print("9. و10. أكثر من عملة ثانوية بنفس الحساب — حساب نقدي متعدد العملات")
print("=" * 70)
s = _fresh_session()
coa, item = _setup_basic(s)
multi_fx = JournalEntry(entry_date=today, ref_no="JV-MULTI", description="حركات متعددة العملات بنفس الحساب",
                         source_type="manual", currency_code="SYP", exchange_rate=1, status=JournalEntryStatus.DRAFT)
s.add(multi_fx); s.flush()
add_manual_line(s, multi_fx, coa["cash"].id, debit=D_("300000"), exchange_rate=1, line_currency_code="SYP")
add_manual_line(s, multi_fx, coa["cash"].id, debit=D_("10"), exchange_rate=D_("15000"), line_currency_code="USD", line_exchange_rate=D_("15000"))
add_manual_line(s, multi_fx, coa["cash"].id, debit=D_("5"), exchange_rate=D_("16000"), line_currency_code="EUR", line_exchange_rate=D_("16000"))
add_manual_line(s, multi_fx, coa["equity"].id if "equity" in coa else s.query(Account).filter_by(code="3101").first().id,
                 credit=D_("530000"), exchange_rate=1)
post_manual_entry(s, multi_fx); s.commit()
stmt = get_account_statement(s, coa["cash"].id, None, today)
expected_total = D_("300000") + money(D_("10")*D_("15000")) + money(D_("5")*D_("16000"))
check("حساب نقدي واحد بـ3 عملات مختلفة معاً: الرصيد الإجمالي بالعملة الأساسية فقط، بلا جمع عملات خام",
      stmt.closing_balance - D_("1000000000.00") == expected_total,
      f"الفرق الفعلي عن رأس المال={stmt.closing_balance - D_('1000000000.00')}, متوقَّع={expected_total}")

# ============================================================================
print(); print("=" * 70); print("11. منع تعديل أي مستند POSTED (فاتورة وقيد يدوي)")
print("=" * 70)
s = _fresh_session()
coa, item = _setup_basic(s)
sale_p = Invoice(invoice_no="SP1", kind=InvoiceKind.SALES, party_name="عميل",
                  invoice_date=today, currency_code="SYP", exchange_rate=1, status=InvoiceStatus.DRAFT)
sale_p.lines = [InvoiceLine(item_id=item.id, quantity=1, unit_price=D_("100"))]
s.add(sale_p); s.commit()
try:
    # ملاحظة Phase 3B-2: هذا الاستدعاء لم يكن يؤثر فعلياً على نتيجة هذا
    # الاختبار أصلاً (كان بـtry/except يبتلع أي استثناء، ولا شيء لاحقاً
    # يتحقق من أثره) — post_sales_invoice يعمل بلا مشكلة على مخزون صفري
    # بمنهجية AVERAGE بأي حال. أُبقيه معطَّلاً بنفس السلوك الدفاعي بدل
    # حذفه بصمت، وأوثّق أنه فعلياً بلا أثر على الاختبار.
    default_wh11 = get_default_warehouse(s)
    post_opening_inventory(s, [OpeningInventoryLineInput(
        item_id=item.id, warehouse_id=default_wh11.id, quantity=D_("1"), unit_cost_foreign=D_("1"))], today)
except Exception:
    pass
post_sales_invoice(s, sale_p, is_cash=True); s.commit()
try:
    ensure_invoice_editable(sale_p)
    check("فاتورة POSTED تُمنع من التعديل", False, "لم تُرفَض!")
except EditNotAllowedError as e:
    check("فاتورة POSTED تُمنع من التعديل", True, str(e))

manual_p = JournalEntry(entry_date=today, ref_no="JV-P1", description="قيد للاختبار",
                         source_type="manual", currency_code="SYP", exchange_rate=1, status=JournalEntryStatus.DRAFT)
s.add(manual_p); s.flush()
add_manual_line(s, manual_p, coa["cash"].id, debit=D_("10"), exchange_rate=1)
add_manual_line(s, manual_p, coa["sales"].id, credit=D_("10"), exchange_rate=1)
post_manual_entry(s, manual_p); s.commit()
try:
    add_manual_line(s, manual_p, coa["cash"].id, debit=D_("5"), exchange_rate=1)
    check("قيد يدوي POSTED يُمنع من إضافة سطر جديد", False, "لم يُرفَض!")
except Exception as e:
    check("قيد يدوي POSTED يُمنع من إضافة سطر جديد", "لا يمكن تعديله" in str(e), str(e))

# ============================================================================
print(); print("=" * 70); print("12. عكس قيد مرحّل ثم التأكد أن التقارير لا تتأثر مرتين")
print("=" * 70)
s = _fresh_session()
coa, item = _setup_basic(s)
tb_before = get_trial_balance(s)
cash_before = next((r.total_debit - r.total_credit for r in tb_before.rows if r.account.id == coa["cash"].id), D_("0"))

rev_entry = JournalEntry(entry_date=today, ref_no="JV-REVME", description="قيد سيُعكَس",
                          source_type="manual", currency_code="SYP", exchange_rate=1, status=JournalEntryStatus.DRAFT)
s.add(rev_entry); s.flush()
add_manual_line(s, rev_entry, coa["cash"].id, debit=D_("777"), exchange_rate=1)
add_manual_line(s, rev_entry, coa["sales"].id, credit=D_("777"), exchange_rate=1)
post_manual_entry(s, rev_entry); s.commit()

reversal = reverse_manual_entry(s, rev_entry, today + datetime.timedelta(days=1))
s.commit()

tb_after = get_trial_balance(s)
cash_after = next((r.total_debit - r.total_credit for r in tb_after.rows if r.account.id == coa["cash"].id), D_("0"))
check("بعد عكس القيد: صافي الصندوق يعود تماماً لما كان عليه قبل القيد الأصلي (لا تأثير مزدوج)",
      cash_after == cash_before, f"قبل={cash_before}, بعد العكس={cash_after}")
check("قيد العكس نفسه متوازن ومرحّل", reversal.status == JournalEntryStatus.POSTED and reversal.is_balanced())

# ============================================================================
print(); print("=" * 70); print("13. التأكد أن المسودات لا تظهر بأي تقرير مالي")
print("=" * 70)
s = _fresh_session()
coa, item = _setup_basic(s)
tb_clean = get_trial_balance(s)
cash_clean = next((r.total_debit - r.total_credit for r in tb_clean.rows if r.account.id == coa["cash"].id), D_("0"))

draft_entry = JournalEntry(entry_date=today, ref_no="JV-DRAFT", description="مسودة لن تُرحَّل أبداً",
                            source_type="manual", currency_code="SYP", exchange_rate=1, status=JournalEntryStatus.DRAFT)
s.add(draft_entry); s.flush()
add_manual_line(s, draft_entry, coa["cash"].id, debit=D_("999999"), exchange_rate=1)
add_manual_line(s, draft_entry, coa["sales"].id, credit=D_("999999"), exchange_rate=1)
s.commit()  # نحفظها كمسودة فقط — بلا post_manual_entry إطلاقاً

tb_with_draft = get_trial_balance(s)
cash_with_draft = next((r.total_debit - r.total_credit for r in tb_with_draft.rows if r.account.id == coa["cash"].id), D_("0"))
check("قيد بحالة DRAFT لا يظهر بميزان المراجعة إطلاقاً", cash_with_draft == cash_clean,
      f"قبل={cash_clean}, بعد إضافة مسودة (يجب ألا تتغيّر)={cash_with_draft}")

# ============================================================================
print(); print("=" * 70); print("14. إعادة فتح مسودة بعد تغيّر إعدادات سعر الصرف — القيمة المحفوظة لا تتغيّر تلقائياً")
print("=" * 70)
s = _fresh_session()
coa, item = _setup_basic(s)
draft2 = JournalEntry(entry_date=today, ref_no="JV-DRAFT2", description="مسودة بسعر صرف محدَّد",
                       source_type="manual", currency_code="USD", exchange_rate=D_("15000"), status=JournalEntryStatus.DRAFT)
s.add(draft2); s.flush()
add_manual_line(s, draft2, coa["cash"].id, debit=D_("10"), exchange_rate=D_("15000"),
                 line_currency_code="USD", line_exchange_rate=D_("15000"))
add_manual_line(s, draft2, coa["sales"].id, credit=money(D_("10")*D_("15000")), exchange_rate=1)
s.commit()
stored_rate_before = draft2.lines[0].line_exchange_rate
stored_base_before = draft2.lines[0].debit_base
# "تغيير إعداد سعر الصرف العام" لا مكافئ مباشر بالخدمة الحالية (لا جدول
# إعدادات أسعار صرف بعد — راجع WORKFLOW.md §21)، فنحاكيه بأبسط شكل: مجرد
# قراءة المسودة مجدداً من القاعدة والتأكد أن قيمها لم تتغيّر من تلقاء نفسها
s.expire_all()
draft2_reloaded = s.get(JournalEntry, draft2.id)
check("سعر الصرف المحفوظ بسطر المسودة لا يتغيّر تلقائياً بمجرد إعادة القراءة",
      draft2_reloaded.lines[0].line_exchange_rate == stored_rate_before and
      draft2_reloaded.lines[0].debit_base == stored_base_before)

# ============================================================================
print(); print("=" * 70); print("15. تكلفة المخزون دائماً بالعملة الأساسية مهما كانت عملة الفاتورة (SYP/USD/EUR معاً)")
print("=" * 70)
s = _fresh_session()
coa, item = _setup_basic(s)
scenarios = [("SYP", D_("1"), D_("100")), ("USD", D_("15000"), D_("8")), ("EUR", D_("16300"), D_("6"))]
total_qty, total_cost = D_("0"), D_("0")
for i, (ccy, rate, price) in enumerate(scenarios):
    inv = Invoice(invoice_no=f"MC-{i}", kind=InvoiceKind.PURCHASE, party_name=f"مورد {ccy}",
                  invoice_date=today - datetime.timedelta(days=10 - i), currency_code=ccy,
                  exchange_rate=rate, status=InvoiceStatus.DRAFT)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=5, unit_price=price)]
    s.add(inv); s.commit()
    post_purchase_invoice(s, inv, is_cash=True); s.commit()
    total_qty += D_("5")
    total_cost += money(D_("5") * price * rate)

summary15 = get_item_stock_summary(s, item.id)
expected_avg15 = total_cost / total_qty
check("متوسط تكلفة صحيح عبر 3 عملات مختلفة تماماً بنفس المادة (كلها محوَّلة للأساسية أولاً)",
      abs(summary15.average_cost - expected_avg15) < D_("0.01"),
      f"فعلي={summary15.average_cost}, متوقَّع={expected_avg15}")

print()
print("=" * 70)
print(f"✅ كل الحالات المركَّزة الـ15 نجحت ({len(results)} تحقّقاً فرعياً)")
print("=" * 70)
