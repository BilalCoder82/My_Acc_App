"""
tests/test_movement_date_type_defect_characterization.py
=============================================================
توصيف خلل حقيقي اكتُشِف أثناء بناء اختبارات 3B-5-F، أُعيد فتح 3B-5-E
بسببه (Evidence-based reopening، لا تعديل تصميم): فاتورتان بنفس
movement_date بالضبط، عبر المسار الإنتاجي الحقيقي (لا بناء يدوي)،
بفلشين/commit منفصلين — كانتا تُبلِّغان خطأً عن بعضهما كـCandidate
بـ3B-5-E (يخالف افتراض Finding B/ID-002 صراحة).

جذر الخلل (كان، قبل الإصلاح):
- InventoryMovement.movement_date مُعلَن Mapped[datetime]، لكن كل
  التسعة مواقع الإنشاء كانت تُمرِّر date مجرَّداً (Invoice.invoice_date،
  StockTransfer.transfer_date، ومعاملات cancel_date/opening_date/
  reversal_date -- كلها date، لا datetime).
- القيمة المخزَّنة بالقاعدة كانت صحيحة دائماً (تحويل الكتابة يعمل).
- الكائن الموجود بالذاكرة (session.info بقائمة hook الاكتشاف) لم يكن
  يُنعَش بعد flush، فيبقى date مجرَّداً وقت مقارنة الاكتشاف.
- SQLite كان يقارن نصياً: '2026-01-05 00:00:00.000000' > '2026-01-05'
  يُقيَّم True رغم تطابق التاريخ التقويمي تماماً.

الإصلاح المُطبَّق (الخيار C — تطبيع مركزي، @validates على
InventoryMovement.movement_date بـapp/models.py): أي date يُسنَد يتحوَّل
فوراً لكائن datetime حقيقي (datetime.combine(value, time.min))، بلا
تنسيق نصّي يدوي، بلا تغيير schema/migration. هذا الملف الآن يُثبِت
PASS فعلياً بعد الإصلاح، ويبقى بالـregression الدائم لمنع الانتكاس.
"""
import os, sys, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Setting, CostMethod, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus,
    CorrectionEvent,
)
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_purchase_invoice, get_default_warehouse
from app.services.correction_detection import attach_detection_listeners

results = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((status, label, detail))
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))


def fresh_session():
    attach_detection_listeners()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed(session):
    coa = create_default_chart_of_accounts(session)
    session.add(Setting(key="base_currency", value="SYP"))
    session.commit()
    wh = get_default_warehouse(session)
    return coa, wh


def purchase(session, item, wh, qty_, price, d, ref):
    inv = Invoice(invoice_no=ref, kind=InvoiceKind.PURCHASE, party_name="مورد",
                  invoice_date=d, currency_code="SYP", exchange_rate=D_("1"),
                  status=InvoiceStatus.DRAFT, warehouse_id=wh.id)
    inv.lines = [InvoiceLine(item_id=item.id, quantity=D_(str(qty_)), unit_price=D_(str(price)))]
    session.add(inv); session.commit()
    post_purchase_invoice(session, inv, is_cash=True)
    session.commit()
    return inv


# ===========================================================================
# السيناريو المطلوب حرفياً: فاتورة A (2026-01-05)، فاتورة B (نفس التاريخ
# بالضبط)، مسار الإنتاج الحقيقي، flush/commit منفصلان لكل واحدة.
# ===========================================================================
SAME_DAY = datetime.date(2026, 1, 5)

s = fresh_session()
coa, wh = seed(s)
item = create_item(
    s, sku="DEFECT", name_ar="صنف", unit="قطعة",
    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id,
    sales_account_id=coa["sales"].id, cost_method=CostMethod.AVERAGE,
)

purchase(s, item, wh, 5, 10, SAME_DAY, "DEFECT-A")   # فلش/commit منفصل أول
purchase(s, item, wh, 7, 10, SAME_DAY, "DEFECT-B")   # فلش/commit منفصل ثانٍ -- نفس التاريخ بالضبط

events = s.execute(select(CorrectionEvent).where(CorrectionEvent.item_id == item.id)).scalars().all()

check(
    "لا CorrectionEvent يُنشَأ بين فاتورتين بنفس movement_date عبر فلشين منفصلين",
    len(events) == 0,
    f"عدد الأحداث الفعلي={len(events)} (متوقَّع=0؛ الحالة الحالية تُثبِت الخلل بوجود حدث واحد على الأقل)",
)

print()
print(f"Characterization summary: {sum(1 for r in results if r[0]=='PASS')}/{len(results)}")
failed = [r for r in results if r[0] == "FAIL"]
if failed:
    print("FAILURES:")
    for r in failed:
        print(" -", r[1], r[2])
    sys.exit(1)
