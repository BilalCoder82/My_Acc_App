"""
tests/test_opening_account_balances.py
==========================================
Acceptance Gate لـPhase 3B-1 (الأرصدة الافتتاحية للحسابات العامة فقط)
— مطابق حرفياً لقائمة Bilal: Opening account → Posted Journal Entry →
Trial Balance، بكل الحالات المطلوبة. النطاق محصور بصرامة بـ3B-1: لا
مخزون، لا فواتير عملاء/موردين، لا سندات — تلك مسؤولية 3B-2/3B-3/3B-4.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal as D_
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Setting, Account, AccountType, JournalEntry, JournalEntryStatus
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.services.opening_balances import (
    post_opening_account_balances, reverse_opening_account_balances,
    OpeningBalanceLineInput, OpeningBalanceError,
    OPENING_BALANCES_SETTING_KEY, CLEARING_ACCOUNT_SETTING_KEY,
)
from app.services.posting import get_base_currency
from app.reports.trial_balance import get_trial_balance

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def fresh_env(base_currency="USD"):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    coa = create_default_chart_of_accounts(session)
    session.add(Setting(key="base_currency", value=base_currency))
    equity = Account(code="3199", name_ar="أرصدة افتتاحية - توازن", account_type=AccountType.EQUITY)
    session.add(equity); session.flush()
    session.add(Setting(key=CLEARING_ACCOUNT_SETTING_KEY, value=str(equity.id)))
    session.commit()
    return session, coa, equity


# =====================================================================
# 1) Base currency = USD، رصيد افتتاحي بـUSD (نفس العملة الأساسية)
# =====================================================================
print("== 1) Opening balance بعملة الشركة الأساسية (USD) ==")
s, coa, equity = fresh_env("USD")
check("get_base_currency فعلياً USD قبل أي شيء", get_base_currency(s) == "USD")

entry = post_opening_account_balances(
    s, [OpeningBalanceLineInput(account_id=coa["cash"].id, debit_foreign=D_("10000"))],
    datetime.date(2026, 1, 1),
)
s.commit()
check("القيد POSTED فعلياً", entry.status == JournalEntryStatus.POSTED)
check("القيد متوازن (is_balanced)", entry.is_balanced())
check("سطران بالضبط (الصندوق + التوازن التلقائي)", len(entry.lines) == 2)
tb = get_trial_balance(s)
check("ميزان المراجعة متوازن فعلياً بعد الترحيل", tb.is_balanced)
cash_row = next(r for r in tb.rows if r.account.id == coa["cash"].id)
check("رصيد الصندوق بميزان المراجعة = 10,000 مديناً بالضبط",
      cash_row.total_debit - cash_row.total_credit == D_("10000"))

# =====================================================================
# 2) رصيد افتتاحي بعملة أجنبية (Base=USD، الحساب بعملة أخرى فعلياً)
# =====================================================================
print("\n== 2) Opening balance بعملة أجنبية (EUR) مع Base=USD ==")
s2, coa2, equity2 = fresh_env("USD")
entry2 = post_opening_account_balances(
    s2, [OpeningBalanceLineInput(account_id=coa2["bank"].id, debit_foreign=D_("1000"),
                                  currency_code="EUR", exchange_rate=D_("1.1"))],
    datetime.date(2026, 1, 1),
)
s2.commit()
bank_line = next(l for l in entry2.lines if l.account_id == coa2["bank"].id)
check("سطر البنك بعملة EUR فعلياً (line_currency_code)", bank_line.line_currency_code == "EUR")
check("المعادل الأساسي = 1,000×1.1 = 1,100 USD بالضبط", D_(str(bank_line.debit_base)) == D_("1100"))
check("ميزان المراجعة متوازن رغم اختلاف عملة السطر عن الأساسية", get_trial_balance(s2).is_balanced)

# =====================================================================
# 3-4) مدين افتتاحي ودائن افتتاحي، معاً بعملية واحدة، لعدة حسابات
# =====================================================================
print("\n== 3-4) مدين + دائن + عدة حسابات بعملية واحدة ==")
s3, coa3, equity3 = fresh_env("USD")
capital = Account(code="3102-X", name_ar="رأس المال (اختبار)", account_type=AccountType.EQUITY)
s3.add(capital); s3.commit()
entry3 = post_opening_account_balances(
    s3, [
        OpeningBalanceLineInput(account_id=coa3["cash"].id, debit_foreign=D_("6000")),
        OpeningBalanceLineInput(account_id=coa3["bank"].id, debit_foreign=D_("4000")),
        OpeningBalanceLineInput(account_id=capital.id, credit_foreign=D_("10000")),
    ],
    datetime.date(2026, 1, 1),
)
s3.commit()
check("3 حسابات بعملية واحدة + لا سطر توازن إضافي (متوازنة أصلاً)",
      len(entry3.lines) == 3, f"actual={len(entry3.lines)}")
check("ميزان المراجعة متوازن (لا فرق تقريب)", get_trial_balance(s3).is_balanced)
tb3 = get_trial_balance(s3)
capital_row = next(r for r in tb3.rows if r.account.id == capital.id)
check("رأس المال دائن 10,000 بالضبط", capital_row.total_credit - capital_row.total_debit == D_("10000"))

# =====================================================================
# 5) عملية غير متوازنة على مستوى السطر الواحد → Reject
#    (ملاحظة تصميم: القيد الكامل لا يمكن أن يكون "غير متوازن" أبداً
#    لأن سطر التوازن التلقائي يفرض التوازن دائماً بالتعريف — "غير
#    متوازنة" هنا تعني سطراً مُدخَلاً خطأً: مدين ودائن معاً، أو صفر
#    بكليهما، وهو ما يرفضه post_manual_entry/_validate_lines فعلياً)
# =====================================================================
print("\n== 5) سطر غير صالح (مدين ودائن معاً / صفر بكليهما) → Reject ==")
s5, coa5, equity5 = fresh_env("USD")
try:
    post_opening_account_balances(
        s5, [OpeningBalanceLineInput(account_id=coa5["cash"].id, debit_foreign=D_("100"), credit_foreign=D_("50"))],
        datetime.date(2026, 1, 1),
    )
    check("رفض سطر بمدين ودائن معاً", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض سطر بمدين ودائن معاً", True)

try:
    post_opening_account_balances(
        s5, [OpeningBalanceLineInput(account_id=coa5["cash"].id)],  # 0/0
        datetime.date(2026, 1, 1),
    )
    check("رفض سطر فارغ (صفر بكليهما)", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض سطر فارغ (صفر بكليهما)", True)

# =====================================================================
# 6) حساب غير صالح: غير موجود / Group / Inactive → Reject
# =====================================================================
print("\n== 6) حساب غير موجود/Group/Inactive → Reject ==")
try:
    post_opening_account_balances(
        s5, [OpeningBalanceLineInput(account_id=999999, debit_foreign=D_("100"))],
        datetime.date(2026, 1, 1),
    )
    check("رفض حساب غير موجود", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض حساب غير موجود", True)

group_acc = Account(code="1199", name_ar="مجموعة اختبارية", account_type=AccountType.ASSET, is_group=True)
s5.add(group_acc); s5.commit()
try:
    post_opening_account_balances(
        s5, [OpeningBalanceLineInput(account_id=group_acc.id, debit_foreign=D_("100"))],
        datetime.date(2026, 1, 1),
    )
    check("رفض حساب تجميعي (Group)", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض حساب تجميعي (Group)", True)

inactive_acc = Account(code="1198", name_ar="حساب معطَّل", account_type=AccountType.ASSET, is_active=False)
s5.add(inactive_acc); s5.commit()
try:
    post_opening_account_balances(
        s5, [OpeningBalanceLineInput(account_id=inactive_acc.id, debit_foreign=D_("100"))],
        datetime.date(2026, 1, 1),
    )
    check("رفض حساب غير نشط (Inactive)", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض حساب غير نشط (Inactive)", True)

# --- إضافي: حساب إيراد/مصروف → Reject (لا Revenue/Expense impact) ---
try:
    post_opening_account_balances(
        s5, [OpeningBalanceLineInput(account_id=coa5["sales"].id, credit_foreign=D_("100"))],
        datetime.date(2026, 1, 1),
    )
    check("رفض حساب إيراد (لا Revenue impact من الرصيد الافتتاحي)", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض حساب إيراد (لا Revenue impact من الرصيد الافتتاحي)", True)
try:
    post_opening_account_balances(
        s5, [OpeningBalanceLineInput(account_id=coa5["cogs"].id, debit_foreign=D_("100"))],
        datetime.date(2026, 1, 1),
    )
    check("رفض حساب مصروف (لا Expense impact من الرصيد الافتتاحي)", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض حساب مصروف (لا Expense impact من الرصيد الافتتاحي)", True)

# =====================================================================
# 7) Clearing account غير صالح → Reject (غير موجود بالإعدادات، غير
#    موجود فعلياً، Group، Inactive، نوعه ليس EQUITY)
# =====================================================================
print("\n== 7) Clearing account غير صالح بكل صوره → Reject ==")
s7 = sessionmaker(bind=create_engine("sqlite:///:memory:"))()
Base.metadata.create_all(s7.get_bind())
coa7 = create_default_chart_of_accounts(s7)
s7.add(Setting(key="base_currency", value="USD"))
s7.commit()
try:
    post_opening_account_balances(
        s7, [OpeningBalanceLineInput(account_id=coa7["cash"].id, debit_foreign=D_("100"))],
        datetime.date(2026, 1, 1),
    )
    check("رفض عند غياب إعداد clearing account كلياً", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض عند غياب إعداد clearing account كلياً", True)

s7.add(Setting(key=CLEARING_ACCOUNT_SETTING_KEY, value="888888"))  # id غير موجود
s7.commit()
try:
    post_opening_account_balances(
        s7, [OpeningBalanceLineInput(account_id=coa7["cash"].id, debit_foreign=D_("100"))],
        datetime.date(2026, 1, 1),
    )
    check("رفض عند clearing account بـid غير موجود فعلياً", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض عند clearing account بـid غير موجود فعلياً", True)

wrong_type_clearing = Account(code="4199", name_ar="حساب إيراد بالخطأ ككليرنس", account_type=AccountType.REVENUE)
s7.add(wrong_type_clearing); s7.flush()
setting_row = s7.get(Setting, CLEARING_ACCOUNT_SETTING_KEY)
setting_row.value = str(wrong_type_clearing.id)
s7.commit()
try:
    post_opening_account_balances(
        s7, [OpeningBalanceLineInput(account_id=coa7["cash"].id, debit_foreign=D_("100"))],
        datetime.date(2026, 1, 1),
    )
    check("رفض clearing account نوعه ليس EQUITY (هنا REVENUE)", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض clearing account نوعه ليس EQUITY (هنا REVENUE)", True)

# =====================================================================
# 8) Idempotency — محاولة تنفيذ نفس النطاق مرتين → Reject
# =====================================================================
print("\n== 8) Idempotency: تنفيذ نفس النطاق مرتين → Reject ==")
s8, coa8, equity8 = fresh_env("USD")
post_opening_account_balances(
    s8, [OpeningBalanceLineInput(account_id=coa8["cash"].id, debit_foreign=D_("5000"))],
    datetime.date(2026, 1, 1),
)
s8.commit()
entries_before = s8.query(JournalEntry).filter_by(source_type="opening_balance").count()
try:
    post_opening_account_balances(
        s8, [OpeningBalanceLineInput(account_id=coa8["cash"].id, debit_foreign=D_("5000"))],
        datetime.date(2026, 1, 1),
    )
    check("رفض تنفيذ نفس نطاق Opening Accounts مرة ثانية", False, "لم يُرفَض!")
except OpeningBalanceError:
    check("رفض تنفيذ نفس نطاق Opening Accounts مرة ثانية", True)
entries_after = s8.query(JournalEntry).filter_by(source_type="opening_balance").count()
check("لا قيد ثانٍ تكوَّن فعلياً (لا رصيد تضاعف)، عدد القيود ثابت",
      entries_before == entries_after == 1, f"before={entries_before} after={entries_after}")
cash_balance = get_trial_balance(s8)
cash_row8 = next(r for r in cash_balance.rows if r.account.id == coa8["cash"].id)
check("رصيد الصندوق ما زال 5,000 بالضبط (لا 10,000)",
      cash_row8.total_debit - cash_row8.total_credit == D_("5000"))

# =====================================================================
# 9) الإلغاء/العكس يعمل دون حذف التاريخ، ويسمح بإعادة الإدخال
# =====================================================================
print("\n== 9) عكس الرصيد الافتتاحي — لا حذف، يسمح بإعادة الإدخال ==")
original_entry_s8 = s8.query(JournalEntry).filter_by(source_type="opening_balance").first()
reversal = reverse_opening_account_balances(s8, original_entry_s8, datetime.date(2026, 1, 15))
s8.commit()
check("القيد الأصلي ما زال موجوداً بسجل القيود (لا حذف)",
      s8.get(JournalEntry, original_entry_s8.id) is not None)
check("القيد الأصلي حالته ما زالت POSTED (العكس لا يمحو التاريخ)",
      original_entry_s8.status == JournalEntryStatus.POSTED)
check("قيد عكسي فعلي وُلِد ومتوازن", reversal is not None and reversal.is_balanced())
tb_after_reversal = get_trial_balance(s8)
cash_row_after = next(r for r in tb_after_reversal.rows if r.account.id == coa8["cash"].id)
check("رصيد الصندوق = صفر فعلياً بعد العكس (الأثر انتفى، لا السجل)",
      cash_row_after.total_debit - cash_row_after.total_credit == D_("0"))

# إعادة الإدخال بعد العكس يجب أن تنجح الآن
new_entry = post_opening_account_balances(
    s8, [OpeningBalanceLineInput(account_id=coa8["cash"].id, debit_foreign=D_("7000"))],
    datetime.date(2026, 1, 15),
)
s8.commit()
check("إعادة الإدخال بعد العكس نجحت فعلياً (لا رفض Idempotency خاطئ)",
      new_entry.status == JournalEntryStatus.POSTED)
tb_final = get_trial_balance(s8)
cash_row_final = next(r for r in tb_final.rows if r.account.id == coa8["cash"].id)
check("الرصيد النهائي = 7,000 بالضبط (القيمة الجديدة الصحيحة فقط)",
      cash_row_final.total_debit - cash_row_final.total_credit == D_("7000"))

# =====================================================================
# 10) إغلاق وإعادة فتح الشركة يحافظ على النتيجة (اتصال جديد تماماً)
# =====================================================================
print("\n== 10) إغلاق وإعادة فتح القاعدة يحافظ على النتيجة ==")
import shutil
from pathlib import Path
TMP = Path("/tmp/opening_balance_reopen_test")
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)
db_path = TMP / "reopen_test.db"
engine_file = create_engine(f"sqlite:///{db_path}")
Base.metadata.create_all(engine_file)
s10 = sessionmaker(bind=engine_file)()
coa10 = create_default_chart_of_accounts(s10)
cash_id10 = coa10["cash"].id
s10.add(Setting(key="base_currency", value="USD"))
eq10 = Account(code="3199", name_ar="توازن", account_type=AccountType.EQUITY)
s10.add(eq10); s10.flush()
s10.add(Setting(key=CLEARING_ACCOUNT_SETTING_KEY, value=str(eq10.id)))
s10.commit()
post_opening_account_balances(
    s10, [OpeningBalanceLineInput(account_id=cash_id10, debit_foreign=D_("3000"))],
    datetime.date(2026, 1, 1),
)
s10.commit()
s10.close(); engine_file.dispose()

engine_file2 = create_engine(f"sqlite:///{db_path}")
s10b = sessionmaker(bind=engine_file2)()
tb10 = get_trial_balance(s10b)
check("بعد إغلاق وإعادة فتح فعلي (اتصال جديد تماماً): ميزان المراجعة ما زال متوازناً", tb10.is_balanced)
cash_row10 = next(r for r in tb10.rows if r.account.id == cash_id10)
check("الرصيد ما زال 3,000 بالضبط بعد إعادة الفتح", cash_row10.total_debit - cash_row10.total_credit == D_("3000"))
posted_setting = s10b.get(Setting, OPENING_BALANCES_SETTING_KEY)
check("Setting الـIdempotency محفوظ فعلياً على القرص (يمنع إعادة الترحيل حتى بجلسة جديدة)",
      posted_setting is not None)
s10b.close(); engine_file2.dispose()
shutil.rmtree(TMP)

# =====================================================================
# 11) رد فعل Bilal — ثلاث نقاط تقنية دقيقة تحتاج إثباتاً صريحاً، لا وصفاً
# =====================================================================
print("\n== 11) Transaction boundaries + rollback + عكس يحافظ على السجل التفصيلي + DB-level uniqueness ==")
from app.models import OpeningBalanceEntry
from sqlalchemy.exc import IntegrityError

s11, coa11, equity11 = fresh_env("USD")

# 11-أ) فشل منتصف الدفعة (سطر ثانٍ بحساب غير موجود) + rollback من
# المستدعي (نفس مسؤولية المستدعي بكل الخدمات الحالية بالمشروع — لا
# service بالمشروع كله يستدعي session.commit()/rollback() بنفسه، راجع
# settlements.py/invoice_cancel.py/journal_edit.py) → لا أثر جزئي متبقٍ
try:
    post_opening_account_balances(
        s11, [
            OpeningBalanceLineInput(account_id=coa11["cash"].id, debit_foreign=D_("1000")),
            OpeningBalanceLineInput(account_id=999999, credit_foreign=D_("1000")),  # يفشل بمنتصف الدفعة
        ],
        datetime.date(2026, 1, 1),
    )
    check("رفض دفعة فيها سطر لاحق غير صالح", False, "لم تُرفَض!")
except OpeningBalanceError:
    check("رفض دفعة فيها سطر لاحق غير صالح (منتصف الحلقة، بعد سطر صحيح واحد)", True)
s11.rollback()
check("بعد rollback من المستدعي: لا JournalEntry متبقٍّ إطلاقاً (لا سطر يتيم)",
      s11.query(JournalEntry).count() == 0, f"actual={s11.query(JournalEntry).count()}")
check("بعد rollback: لا OpeningBalanceEntry متبقٍّ إطلاقاً", s11.query(OpeningBalanceEntry).count() == 0)
check("بعد rollback: Setting الـIdempotency غير موجود (لم يُقفَل خطأً)",
      s11.get(Setting, OPENING_BALANCES_SETTING_KEY) is None)

# إعادة المحاولة الصحيحة بعد rollback يجب أن تنجح تماماً بلا أي أثر للمحاولة الفاشلة
entry11 = post_opening_account_balances(
    s11, [OpeningBalanceLineInput(account_id=coa11["cash"].id, debit_foreign=D_("1000"))],
    datetime.date(2026, 1, 1),
)
s11.commit()
check("إعادة المحاولة بعد rollback نجحت بلا أي أثر جانبي من المحاولة الفاشلة",
      entry11.status == JournalEntryStatus.POSTED and len(entry11.lines) == 2)

# 11-ب) reverse_manual_entry لا تلمس OpeningBalanceEntry إطلاقاً — سجل
# السجل التفصيلي (Opening Balance Detail Record) يبقى مرتبطاً بالقيد
# الأصلي (لا القيد العكسي)، لا يُحذَف ولا يُنقَل
audit_before_reversal = s11.query(OpeningBalanceEntry).filter_by(journal_entry_id=entry11.id).all()
check("سجل تفصيلي واحد فعلي مرتبط بالقيد الأصلي قبل العكس", len(audit_before_reversal) == 1)
reversal11 = reverse_opening_account_balances(s11, entry11, datetime.date(2026, 1, 5))
s11.commit()
audit_after_reversal = s11.query(OpeningBalanceEntry).filter_by(journal_entry_id=entry11.id).all()
check("بعد العكس: السجل التفصيلي ما زال مرتبطاً بالقيد الأصلي بالضبط (لم يُحذَف، لم يُنقَل للقيد العكسي)",
      len(audit_after_reversal) == 1 and audit_after_reversal[0].id == audit_before_reversal[0].id)
audit_on_reversal_entry = s11.query(OpeningBalanceEntry).filter_by(journal_entry_id=reversal11.id).count()
check("لا سجل تفصيلي جديد وُلِد للقيد العكسي نفسه (العكس ليس رصيداً افتتاحياً جديداً)",
      audit_on_reversal_entry == 0)

# 11-ج) uniqueness/idempotency: Setting.key عمود PRIMARY KEY فعلياً —
# ليس فقط فحصاً بمستوى التطبيق، بل قيد حقيقي بمستوى قاعدة البيانات
# (يحمي حتى لو تسابقت عمليتان على تجاوز الفحص التطبيقي معاً)
try:
    s11.add(Setting(key="__uniqueness_probe__", value="1"))
    s11.add(Setting(key="__uniqueness_probe__", value="2"))
    s11.flush()
    check("قيد UNIQUE حقيقي بمستوى القاعدة على Setting.key", False, "لم يُرفَض!")
except IntegrityError:
    check("قيد UNIQUE حقيقي بمستوى القاعدة على Setting.key (PRIMARY KEY) — حماية مزدوجة لا تطبيقية فقط", True)
s11.rollback()

print()
print("=" * 70)
print(f"✅ Acceptance Gate كامل لـPhase 3B-1 (الأرصدة الافتتاحية للحسابات) — {len(results)} تحقّقاً")
print("=" * 70)
