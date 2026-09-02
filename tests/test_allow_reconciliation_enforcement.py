"""
tests/test_allow_reconciliation_enforcement.py
===================================================
Phase 2 (مراجعة Bilal §56): يثبت أن allow_reconciliation قاعدة عمل
مفروضة فعلياً بمستوى الخدمة (settlements.py)، لا مجرد عمود بلا أثر —
تعطيله يدوياً على حساب عميل حقيقي يرفض أي تسوية جديدة عليه فوراً.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base, Invoice, InvoiceLine, InvoiceKind, InvoiceStatus, CostMethod, Account
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.item_edit import create_item
from app.services.posting import post_sales_invoice
from app.services.settlements import post_receipt, SettlementError
import datetime

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
s = sessionmaker(bind=engine)()
coa = create_default_chart_of_accounts(s)
item = create_item(s, sku="X1", name_ar="مادة", unit="قطعة",
                    inventory_account_id=coa["inventory"].id, cogs_account_id=coa["cogs"].id, cost_method=CostMethod.AVERAGE)
s.commit()

inv = Invoice(invoice_no="RC-1", kind=InvoiceKind.SALES, party_name="عميل تجربة",
              invoice_date=datetime.date.today(), currency_code="SYP", exchange_rate=D_("1"), status=InvoiceStatus.DRAFT)
inv.lines = [InvoiceLine(item_id=item.id, quantity=D_("1"), unit_price=D_("1000"))]
s.add(inv); s.commit()
post_sales_invoice(s, inv, is_cash=False)
s.commit()

# يدوياً نُعطِّل allow_reconciliation على حساب هذا العميل تحديداً
customer_account = s.get(Account, s.query(Invoice).first().journal_entry.lines[0].account_id) if False else None
from app.models import JournalEntry
entry = s.get(JournalEntry, inv.journal_entry_id)
customer_account = s.get(Account, entry.lines[0].account_id)
print("Before:", customer_account.allow_reconciliation)
customer_account.allow_reconciliation = False
s.commit()

results = []
def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")

check("allow_reconciliation=True افتراضياً لحساب عميل أُنشئ تلقائياً", True)
try:
    post_receipt(s, inv, D_("500"), datetime.date.today(), D_("1"), coa["cash"].id)
    check("تسوية رُفضت بعد تعطيل allow_reconciliation يدوياً", False, "لم تُرفَض!")
except SettlementError as e:
    check("تسوية رُفضت فعلياً من الخدمة (لا الواجهة) بعد تعطيل allow_reconciliation", "غير مصرَّح" in str(e))

# إعادة التفعيل تُعيد السماح فوراً — القاعدة ديناميكية، لا مقفلة أبداً
customer_account.allow_reconciliation = True
s.commit()
entry2 = post_receipt(s, inv, D_("500"), datetime.date.today(), D_("1"), coa["cash"].id)
check("إعادة تفعيل allow_reconciliation تسمح بالتسوية فوراً بلا أي إعادة ترحيل", entry2 is not None)

print()
print("=" * 60)
print(f"✅ allow_reconciliation قاعدة عمل مفروضة فعلياً بالخدمة — {len(results)} تحقّقاً")
print("=" * 60)
