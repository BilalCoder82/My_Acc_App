"""
Multi-tenant Accounting System — Core Schema
==============================================
معمارية: ملف SQLite واحد لكل عميل (شركة)، بالإضافة لملف registry.db مركزي
يحتوي فقط على لائحة العملاء ومسارات ملفاتهم.

القرار المعماري: نظام قيد مزدوج (double-entry) حقيقي مع فرض التوازن
(SUM(debit) == SUM(credit)) على مستوى منطق الحفظ، لأن هذا هو الأساس
الوحيد الذي يسمح لاحقاً بحسابات ختامية وميزان مراجعة صحيحين.
"""

from __future__ import annotations

import enum
from datetime import datetime, date, time

from sqlalchemy import (
    create_engine, ForeignKey, String, Numeric, Date, DateTime,
    Boolean, Enum, Text, CheckConstraint, UniqueConstraint, Integer, Index
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, Session, validates
)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AccountType(str, enum.Enum):
    ASSET = "asset"              # أصول
    LIABILITY = "liability"      # خصوم
    EQUITY = "equity"            # حقوق ملكية
    REVENUE = "revenue"          # إيرادات
    EXPENSE = "expense"          # مصروفات


class AccountSubtype(str, enum.Enum):
    """تصنيف العمل الفرعي للحساب — منفصل تماماً عن AccountType (الطبيعة
    المحاسبية أصل/خصم/... لا تحدد وحدها إن كان الحساب "عميلاً" أو
    "مورداً" أو يسمح بالتسوية). راجع قرار Bilal الصريح: account_type
    وحده ليس Business Rule، ورقم الحساب ليس Business Rule أيضاً — هذا
    الحقل هو مصدر الحقيقة الوحيد لتصنيف العمل، لا استنتاجه من الكود أو
    اسم الحساب بأي مكان بالخدمات.
    """
    GENERAL = "general"      # عام
    CUSTOMER = "customer"    # عميل
    SUPPLIER = "supplier"    # مورد
    CASH = "cash"            # صندوق
    BANK = "bank"            # بنك
    EXPENSE = "expense"      # مصروف
    INCOME = "income"        # إيراد
    OTHER = "other"          # أخرى


class CostMethod(str, enum.Enum):
    FIFO = "fifo"
    AVERAGE = "average"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    POSTED = "posted"           # مرحّلة (ولّدت قيداً محاسبياً)
    CANCELLED = "cancelled"


class InvoiceKind(str, enum.Enum):
    SALES = "sales"
    SALES_RETURN = "sales_return"
    PURCHASE = "purchase"
    PURCHASE_RETURN = "purchase_return"


class MovementDirection(str, enum.Enum):
    IN = "in"
    OUT = "out"


class JournalEntryStatus(str, enum.Enum):
    DRAFT = "draft"
    POSTED = "posted"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# دليل الحسابات — Chart of Accounts
# ---------------------------------------------------------------------------

class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(200))
    account_type: Mapped[AccountType] = mapped_column(Enum(AccountType))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    # عملة "وصفية" للحساب — تخبر المستخدم أي عملة يُتوقَّع أن يتعامل بها هذا
    # الحساب عادةً (مفيد لصندوق دولار مقابل صندوق ليرة مثلاً)، لكنها **غير
    # مفروضة برمجياً بعد**: JournalLine.line_currency_code يقبل أي عملة على
    # أي حساب حالياً، لا تحقق يمنع قيد بعملة مخالفة لعملة الحساب المُعلَنة.
    # فرض هذا التحقق قرار مستقبلي منفصل (لو احتجناه فعلياً)، الحقل موجود
    # وكافٍ لبنائه لاحقاً بدون أي تعديل schema إضافي — راجع WORKFLOW.md.
    currency_code: Mapped[str] = mapped_column(String(3), default="SYP")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # تصنيف العمل الفرعي (§56) — GENERAL افتراضياً لكل الحسابات القديمة
    # (محايد، لا تسوية، لا كسر لأي سلوك حالي). NOT NULL عمداً خلافاً لـ
    # is_cash بـInvoice: هذا تصنيف بنيوي دائم للحساب نفسه، لا حالة عابرة
    # لعملية واحدة — لا معنى لتركه غير معروف.
    subtype: Mapped[AccountSubtype] = mapped_column(Enum(AccountSubtype), default=AccountSubtype.GENERAL)
    # يسمح بتسوية الفواتير — قاعدة عمل صريحة مستقلة عن subtype (قرار
    # Bilal الصريح: subtype وحده لا يقرر، والـService تتحقق من هذا
    # الحقل تحديداً، لا من account_type ولا من رقم الحساب). افتراضياً
    # False حتى للحسابات المصنَّفة CUSTOMER/SUPPLIER — يجب تفعيلها
    # صراحة، لا استنتاجها من التصنيف تلقائياً.
    allow_reconciliation: Mapped[bool] = mapped_column(Boolean, default=False)
    # حساب لا يقبل قيود مباشرة (حساب تجميعي/أب فقط)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)

    parent: Mapped["Account | None"] = relationship(remote_side=[id])
    lines: Mapped[list["JournalLine"]] = relationship(back_populates="account")


# ---------------------------------------------------------------------------
# دليل المواد — Items
# ---------------------------------------------------------------------------

class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20), default="قطعة")
    category: Mapped[str | None] = mapped_column(String(100))
    cost_method: Mapped[CostMethod] = mapped_column(Enum(CostMethod), default=CostMethod.AVERAGE)
    reorder_point: Mapped[float] = mapped_column(Numeric(14, 3), default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # الحسابات المرتبطة بالمادة (لتوليد القيود الآلي)
    inventory_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    sales_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    cogs_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))

    movements: Mapped[list["InventoryMovement"]] = relationship(back_populates="item")


class Warehouse(Base):
    """
    مستودع. حالياً مستودع افتراضي واحد يُنشأ تلقائياً لكل شركة جديدة —
    لا واجهة أو منطق تحويل بين مستودعات بعد. الحقل موجود بالـschema
    فقط حتى لا تحتاج هجرة بيانات لو احتجت لاحقاً تتبع مستودعات فعلية.
    """
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class InventoryMovement(Base):
    """كل حركة مخزون (دخول/خروج) مرتبطة بمصدرها — فاتورة أو تسوية يدوية."""
    __tablename__ = "inventory_movements"
    __table_args__ = (
        # PHASE3B5E: يدعم استعلام الـDetection — راجع
        # 7b3e9d1f4c6a_add_correction_detection_index.py لتبرير ترتيب الأعمدة.
        Index("ix_inventory_movements_item_wh_date", "item_id", "warehouse_id", "movement_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    direction: Mapped[MovementDirection] = mapped_column(Enum(MovementDirection))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3))
    unit_cost: Mapped[float] = mapped_column(Numeric(14, 4))  # كلفة الوحدة وقت الحركة
    movement_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source_type: Mapped[str] = mapped_column(String(30))   # 'sales_invoice' / 'purchase_invoice' / 'opening_balance' / 'opening_inventory'
    source_id: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)

    item: Mapped["Item"] = relationship(back_populates="movements")

    # PHASE3B5E-DEFECT-FIX: عقد العمود المُعلَن هنا هو datetime (السطر
    # أعلاه). كل التسعة مواقع الإنشاء الحالية (posting.py×2, returns.py×2,
    # invoice_cancel.py×1, opening_balances.py×2, inventory_transfer.py×2)
    # تُمرِّر date مجرَّداً فعلياً (Invoice.invoice_date/StockTransfer.
    # transfer_date/معاملات cancel_date وopening_date وreversal_date —
    # كلها date). القيمة المخزَّنة بالقاعدة كانت صحيحة دائماً (تحويل
    # الكتابة يعمل)، لكن الكائن بالذاكرة (قبل أي round-trip من القاعدة)
    # كان يبقى date مجرَّداً — سبَّب مطابقة نصية خاطئة بمقارنات
    # correction_detection.py بين حركتين بنفس التاريخ التقويمي تماماً
    # (SQLite يقارن '...00:00:00.000000' > '...' نصياً، فتُعتبَر السلسلة
    # الأطول "أكبر" رغم تطابق التاريخ). هذا المُحقِّق يفرض العقد المُعلَن
    # فعلياً عند أي إسناد، بصرف النظر عن المصدر — كائن datetime حقيقي عبر
    # datetime.combine()، لا تنسيق نصّي يدوي (isoformat() فشل بتجربة
    # سابقة لنفس السبب بالضبط: عدد أصفار عشرية مختلف). لا تغيير schema/
    # migration — سلوك Python وقت الإسناد فقط.
    @validates("movement_date")
    def _normalize_movement_date(self, key, value):
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, time.min)
        return value


# ---------------------------------------------------------------------------
# القيد المحاسبي — Journal Entry (القلب)
# ---------------------------------------------------------------------------

class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_date: Mapped[date] = mapped_column(Date, default=date.today)
    ref_no: Mapped[str] = mapped_column(String(30), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    currency_code: Mapped[str] = mapped_column(String(3), default="SYP")
    exchange_rate: Mapped[float] = mapped_column(Numeric(14, 6), default=1)
    # مصدر القيد: يدوي أو مولّد آلياً من فاتورة (لمنع التعديل اليدوي على قيود آلية)
    source_type: Mapped[str] = mapped_column(String(30), default="manual")
    source_id: Mapped[int | None] = mapped_column(Integer)
    is_reversal_of: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # القيود اليدوية (سند القيد) تبدأ DRAFT وتُقفل عند الترحيل الصريح.
    # القيود المولّدة آلياً من الفواتير (posting.py) تمرّر POSTED صراحة.
    # الافتراضي DRAFT عمداً — يفرض على أي كود جديد يُنشئ قيداً أن يقرر
    # صراحة حالته، بدل الاعتماد على افتراضي قد يكون خطأ صامتاً.
    status: Mapped[JournalEntryStatus] = mapped_column(
        Enum(JournalEntryStatus), default=JournalEntryStatus.DRAFT
    )

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )

    def is_balanced(self) -> bool:
        """التوازن المحاسبي الصحيح دائماً بالعملة الأساسية (debit_base/credit_base)،
        لا بعملة المعاملة الأصلية — لأن جمع مبالغ بعملات مختلفة (دولار + يورو
        مثلاً) مباشرة بلا تحويل غير منطقي محاسبياً. هذا صحيح للقيد أحادي العملة
        أيضاً (base = amount × 1 حين لا يوجد تحويل)، فلا حاجة لفرع منطق منفصل."""
        from decimal import Decimal
        debit = sum((Decimal(str(l.debit_base)) for l in self.lines), Decimal("0"))
        credit = sum((Decimal(str(l.credit_base)) for l in self.lines), Decimal("0"))
        return (debit - credit).quantize(Decimal("0.01")) == 0


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint(
            "(debit = 0 AND credit >= 0) OR (credit = 0 AND debit >= 0)",
            name="ck_debit_xor_credit",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    debit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    credit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    # القيمة بالعملة الأساسية للشركة = debit/credit × exchange_rate وقت القيد.
    # محفوظة صراحة (لا تُحسب عند العرض) لأن سعر الصرف يتغير لاحقاً، والتقارير
    # التاريخية يجب أن تعكس السعر وقت العملية لا السعر الحالي.
    debit_base: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    credit_base: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    # عملة وسعر صرف خاصان بهذا السطر تحديداً — فقط لسند القيد اليدوي، يسمحان
    # بخلط عملات مختلفة بنفس القيد (مثال: تحويل نقدي دولار مقابل ليرة سورية).
    # NULL يعني "استخدم عملة وسعر صرف القيد الافتراضيين" — القيد أحادي العملة
    # (الحالة الشائعة، وكل الفواتير) لا يحتاج لمس هذين الحقلين إطلاقاً.
    line_currency_code: Mapped[str | None] = mapped_column(String(3))
    line_exchange_rate: Mapped[float | None] = mapped_column(Numeric(14, 6))
    cost_center: Mapped[str | None] = mapped_column(String(50))

    entry: Mapped["JournalEntry"] = relationship(back_populates="lines")
    account: Mapped["Account"] = relationship(back_populates="lines")


# ---------------------------------------------------------------------------
# الفواتير — تولّد قيوداً آلياً، لا تُعامل كسجل محاسبي مستقل
# ---------------------------------------------------------------------------

class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_no: Mapped[str] = mapped_column(String(30), unique=True)
    kind: Mapped[InvoiceKind] = mapped_column(Enum(InvoiceKind))
    invoice_date: Mapped[date] = mapped_column(Date, default=date.today)
    party_name: Mapped[str] = mapped_column(String(200))
    currency_code: Mapped[str] = mapped_column(String(3), default="SYP")
    exchange_rate: Mapped[float] = mapped_column(Numeric(14, 6), default=1)
    status: Mapped[InvoiceStatus] = mapped_column(Enum(InvoiceStatus), default=InvoiceStatus.DRAFT)
    # طريقة الدفع (نقدي/آجل) — كانت اختياراً عابراً بالواجهة فقط يُقرأ
    # لحظة الترحيل، غير مخزَّن؛ إعادة فتح مسودة كانت تُعيده لقيمة الـUI
    # الافتراضية بصمت (§53 — اكتُشف أثناء اختبار الدورة الكاملة). Nullable
    # عمداً: السجلات القديمة (قبل هذه الهجرة) لا تحمل قيمة معروفة، تبقى
    # None ولا يُعاد تخمينها بأثر رجعي.
    is_cash: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # حسم على مستوى الفاتورة كاملة — يُوزَّع نسبياً على البنود عند الحساب
    # (نسبة% أو مبلغ ثابت، نادراً ما يُستخدمان معاً؛ الاثنان محفوظان كبيانات خام)
    discount_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    discount_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    original_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3))
    unit_price: Mapped[float] = mapped_column(Numeric(14, 4))
    # حسم على مستوى البند نفسه (% أو مبلغ ثابت)
    discount_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    discount_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0)

    invoice: Mapped["Invoice"] = relationship(back_populates="lines")
    item: Mapped["Item"] = relationship()

    @property
    def gross_amount(self) -> float:
        """قبل أي حسم — للعرض فقط، الحساب الفعلي بـ invoice_calc.py"""
        return float(self.quantity) * float(self.unit_price)


# ---------------------------------------------------------------------------
# تسوية فاتورة (قبض/دفع) — راجع WORKFLOW.md §42 للقواعد المحاسبية كاملة.
# فاتورة واحدة يمكن أن يكون لها عدة Settlement (دفعات جزئية متعددة).
# لا حقل "balance_due" مخزَّن على الفاتورة عمداً — يُحسَب ديناميكياً دائماً
# من إجمالي الفاتورة (compute_invoice_totals) ناقص مجموع Settlement.amount_foreign
# المرتبطة بها، لتفادي أي احتمال فقدان تزامن بين حقل مخزَّن والواقع.
# ---------------------------------------------------------------------------

class Settlement(Base):
    """قبض/دفع/استرداد — Phase 3B-3: أصبحت هذه الطبقة قادرة على تسوية
    أكثر من هدف (فاتورة و/أو رصيد افتتاحي معاً) بنفس القبضة، عبر
    SettlementAllocation أدناه. invoice_id السابق (كان NOT NULL هنا)
    انتقل بالكامل لـSettlementAllocation.invoice_id — لا عمود مباشر
    للفاتورة هنا بعد الآن؛ راجع get_invoice_balance_due() بـsettlements.py
    التي تُجمِّع الآن من SettlementAllocation لا من هذا الجدول مباشرة.
    """
    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"), unique=True)  # علاقة 1:1 — كل تسوية قيدها المستقل الخاص بها فقط
    party_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))  # إلزامي — Invariant: كل allocations بنفس settlement تخص هذا الحساب بالضبط
    kind: Mapped[str] = mapped_column(String(20))  # "receipt" | "payment" | "customer_refund" | "supplier_refund"
    settlement_date: Mapped[date] = mapped_column(Date, default=date.today)
    currency_code: Mapped[str] = mapped_column(String(3))
    # إجمالي القبضة/الدفعة كاملة، بعملة settlement (== عملة كل الأهداف المخصَّصة لها، مفروض بالخدمة)
    amount_foreign: Mapped[float] = mapped_column(Numeric(14, 2))
    settlement_rate: Mapped[float] = mapped_column(Numeric(14, 6))
    # فرق الصرف الناتج عن هذه التسوية تحديداً (مجموع كل التخصيصات)، بالعملة الأساسية.
    # موجب = ربح صرف، سالب = خسارة صرف (راجع WORKFLOW.md §42.3 للإشارة
    # حسب نوع الحساب: عميل مقابل مورد معكوسان).
    fx_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    __table_args__ = (
        CheckConstraint("amount_foreign > 0", name="ck_settlement_amount_positive"),
        CheckConstraint("settlement_rate > 0", name="ck_settlement_rate_positive"),
    )

    journal_entry: Mapped["JournalEntry"] = relationship()
    party_account: Mapped["Account"] = relationship()
    allocations: Mapped[list["SettlementAllocation"]] = relationship(back_populates="settlement")


class SettlementAllocation(Base):
    """كيف طُبِّق قبض/دفع واحد على هدف واحد (فاتورة أو رصيد افتتاحي) —
    بيانات تشغيلية تفسيرية فقط؛ JournalLine.debit_base/credit_base يبقى
    مصدر الحقيقة المحاسبية (PHASE3B3_DESIGN_SPEC.md §1.13). Append-Only
    — لا UPDATE، لا DELETE، بنفس صرامة Settlement (راجع
    test_settlement_tamper_resistance.py)."""
    __tablename__ = "settlement_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    settlement_id: Mapped[int] = mapped_column(ForeignKey("settlements.id"))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    opening_party_entry_id: Mapped[int | None] = mapped_column(ForeignKey("opening_party_entries.id"), nullable=True)
    amount_foreign: Mapped[float] = mapped_column(Numeric(14, 2))
    fx_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    __table_args__ = (
        CheckConstraint(
            "(invoice_id IS NOT NULL AND opening_party_entry_id IS NULL) OR "
            "(invoice_id IS NULL AND opening_party_entry_id IS NOT NULL)",
            name="ck_settlement_allocation_exclusive_target",
        ),
        CheckConstraint("amount_foreign > 0", name="ck_settlement_allocation_amount_positive"),
    )

    settlement: Mapped["Settlement"] = relationship(back_populates="allocations")
    invoice: Mapped["Invoice | None"] = relationship()
    opening_party_entry: Mapped["OpeningPartyEntry | None"] = relationship()


class OpeningPartyKind(str, enum.Enum):
    RECEIVABLE = "receivable"   # العميل مدين لنا
    PAYABLE = "payable"         # نحن مدينون للمورد


class OpeningPartyEntry(Base):
    """رصيد افتتاحي لعميل/مورد — Phase 3B-3. سجل تفصيلي مستقل تماماً
    (لا جدول Receivable/Payable منفصلين)، بـJournalEntry مستقل خاص به
    (لا قيد مُجمَّع لعدة أرصدة — PHASE3B3_DESIGN_SPEC.md §1.5). ليس
    Invoice وهمية — InvoiceLine.item_id يبقى NOT NULL بلا مساس."""
    __tablename__ = "opening_party_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"), unique=True)  # علاقة 1:1 — كل رصيد افتتاحي قيده المستقل الخاص به فقط (§1.5)
    party_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[OpeningPartyKind] = mapped_column(Enum(OpeningPartyKind))
    reference: Mapped[str] = mapped_column(String(100))  # وصفي/تتبعي فقط — بلا UNIQUE عمداً (قرار Bilal)
    original_amount_foreign: Mapped[float] = mapped_column(Numeric(14, 2))
    currency_code: Mapped[str] = mapped_column(String(3))
    exchange_rate: Mapped[float] = mapped_column(Numeric(14, 6), default=1)
    amount_base: Mapped[float] = mapped_column(Numeric(14, 2))  # محسوبة مرة واحدة وقت الإدخال، لا يُعاد تسعيرها لاحقاً
    opening_date: Mapped[date] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint("original_amount_foreign > 0", name="ck_opening_party_amount_positive"),
        CheckConstraint("exchange_rate > 0", name="ck_opening_party_rate_positive"),
    )

    journal_entry: Mapped["JournalEntry"] = relationship()
    party_account: Mapped["Account"] = relationship()


# ---------------------------------------------------------------------------
# تحويل بين مستودعات — لا تأثير محاسبي (حركة داخلية فقط)
# ---------------------------------------------------------------------------

class StockTransfer(Base):
    __tablename__ = "stock_transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    transfer_no: Mapped[str] = mapped_column(String(30), unique=True)
    transfer_date: Mapped[date] = mapped_column(Date, default=date.today)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    from_warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    to_warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3))
    note: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# الإعدادات — Settings (مفتاح/قيمة بسيط لكل شركة)
# ---------------------------------------------------------------------------
# الأرصدة الافتتاحية للحسابات (Phase 3B-1) — سجل تفصيلي (Opening Balance
# Detail Record)، لا "سجل تدقيق" (Audit Log) بالمعنى الكامل — لا يحل محل
# JournalEntry/JournalLine (القيد الفعلي هو مصدر الحقيقة المحاسبية
# دائماً)، فقط يحفظ المُدخَل الأصلي كما أدخله المستخدم (الحساب/العملة/
# المبلغ/السعر) لعرضه لاحقاً. لا يحمل من/متى/أي عملية (created_by/
# created_at/scope id) — لو احتجنا Audit Trail حقيقياً لاحقاً (من نفَّذ،
# ومتى بالضبط)، يحتاج حقولاً إضافية صريحة، لا افتراض أن هذا الجدول
# يوفرها ضمناً (مراجعة Bilal، لا تصحيحاً لخطأ سابق — التسمية فقط كانت
# مضللة قليلاً، البنية والغرض صحيحان).
# ---------------------------------------------------------------------------

class JournalNumberSequence(Base):
    """PHASE3B4_DESIGN_SPEC.md §1 — عدّاد ترقيم منفصل لكل namespace
    (JV-OPEN، JV-REV، JE-SAL، ...). التحديث الفعلي (+1) يتم عبر اتصال
    SQLite مستقل تماماً (راجع journal_edit.py::_reserve_ref_no)، لا عبر
    هذا الـORM model مباشرة — الحجز يحتاج Transaction مستقلة عن جلسة
    المستند (§1/§4)، وSQLAlchemy Session المشتركة لا تحقق هذا الاستقلال
    تلقائياً. الـmodel موجود هنا للتوثيق البنيوي وأي استعلام قراءة فقط."""
    __tablename__ = "journal_number_sequences"

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace: Mapped[str] = mapped_column(String(30), unique=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


class OpeningBalanceEntry(Base):
    __tablename__ = "opening_balance_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    currency_code: Mapped[str] = mapped_column(String(3))
    debit_foreign: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    credit_foreign: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    exchange_rate: Mapped[float] = mapped_column(Numeric(14, 6), default=1)
    base_equivalent: Mapped[float] = mapped_column(Numeric(14, 2))
    opening_date: Mapped[date] = mapped_column(Date)

    journal_entry: Mapped["JournalEntry"] = relationship()
    account: Mapped["Account"] = relationship()


class OpeningInventoryEntry(Base):
    """سجل تفصيلي (Detail Record) للأرصدة الافتتاحية للمخزون — Phase 3B-2.
    ليس مصدر الحقيقة المحاسبية (JournalEntry/JournalLine و
    InventoryMovement هما المصدر)، فقط يحتفظ بما أدخله المستخدم أصلاً
    لكل سطر (مادة+مستودع)، ويربطه مباشرة بالحركة الفعلية الناتجة عنه
    (inventory_movement_id) — إضافة عن نمط OpeningBalanceEntry بـ3B-1،
    لأن كل سطر هنا يُنتِج حركة مخزون منفصلة فعلياً."""
    __tablename__ = "opening_inventory_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    inventory_movement_id: Mapped[int] = mapped_column(ForeignKey("inventory_movements.id"))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3))
    unit_cost_foreign: Mapped[float] = mapped_column(Numeric(14, 4))
    currency_code: Mapped[str] = mapped_column(String(3))
    exchange_rate: Mapped[float] = mapped_column(Numeric(14, 6), default=1)
    unit_cost_base: Mapped[float] = mapped_column(Numeric(14, 4))
    opening_date: Mapped[date] = mapped_column(Date)

    journal_entry: Mapped["JournalEntry"] = relationship()
    item: Mapped["Item"] = relationship()
    warehouse: Mapped["Warehouse"] = relationship()
    inventory_movement: Mapped["InventoryMovement"] = relationship()


# ---------------------------------------------------------------------------

class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------------------
# PHASE 3B-5-C — Correction Event (schema only)
# ---------------------------------------------------------------------------
# هذا القسم Schema فقط — بلا أي منطق Detection/Analysis/Recalculation.
# التصميم الدلالي الكامل والمبررات موثّقة في
# 3B-5-C_DISCOVERY_DESIGN_QUESTIONS.md (Q1–Q3.6 + Pre-Implementation Schema
# Audit). لا تُضف سلوكاً هنا لم يُقرَّر بعد في تلك الوثيقة.

class CorrectionEventStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    ANALYSIS = "analysis"
    PENDING = "pending"
    APPROVED = "approved"
    ACCOUNTING_CORRECTION = "accounting_correction"
    STALE = "stale"
    WITHDRAWN = "withdrawn"


class RootCauseComponentType(str, enum.Enum):
    NEW_MOVEMENT = "new_movement"
    REVERSAL = "reversal"
    REPLACEMENT = "replacement"


class ChronologyBasis(str, enum.Enum):
    KNOWN = "known"
    ASSUMED = "assumed"


class ScopeCompleteness(str, enum.Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class ImpactScopeElementKind(str, enum.Enum):
    MOVEMENT = "movement"
    DOCUMENT = "document"
    ACCOUNTING_EFFECT = "accounting_effect"


class ImpactScopeRelationshipType(str, enum.Enum):
    CAUSES = "causes"
    PRECEDES = "precedes"
    PROPAGATES_TO = "propagates_to"
    BELONGS_TO = "belongs_to"
    PRODUCES_ACCOUNTING_EFFECT = "produces_accounting_effect"


class CorrectionEvent(Base):
    """الكيان الجذري. Item-scoped دائماً (راجع تدقيق Item Scope في الوثيقة).
    status هو الحقل الوحيد القابل للتغيير طوال دورة الحياة؛ كل شيء آخر عن
    الحدث نفسه (item، root causes) يثبت عند الإنشاء."""
    __tablename__ = "correction_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False, index=True)
    trigger_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[CorrectionEventStatus] = mapped_column(
        Enum(CorrectionEventStatus), default=CorrectionEventStatus.CANDIDATE, index=True
    )
    chronology_basis: Mapped[ChronologyBasis | None] = mapped_column(Enum(ChronologyBasis))
    scope_completeness: Mapped[ScopeCompleteness | None] = mapped_column(Enum(ScopeCompleteness))
    analysis_as_of: Mapped[datetime | None] = mapped_column(DateTime)
    # لا FK حقيقي هنا عمداً (وليس سهواً): approved_candidate_state_id يشير إلى
    # correction_event_candidate_states، التي بدورها تشير إلى هذا الجدول —
    # علاقة دائرية حقيقية بين جدولين جديدين معاً. بدل الالتفاف بإنشاء الجداول
    # على مرحلتين في migration (batch mode على SQLite يعيد بناء الجدول بالكامل
    # لكل ALTER، مما يعقّد الترتيب الدائري بلا داعٍ)، عولجت بنفس أسلوب
    # source_type/source_id المرجعي القائم فعلاً في المشروع: عمود عادي بلا قيد
    # FK على مستوى القاعدة، إنفاذ الإشارة يبقى مسؤولية طبقة الخدمة لاحقاً.
    # يبقى NULL طوال CANDIDATE/ANALYSIS/PENDING/STALE — لا مرشّح "مفضَّل" حتى
    # لحظة الموافقة الصريحة فقط.
    approved_candidate_state_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    item: Mapped["Item"] = relationship(foreign_keys=[item_id], viewonly=True)
    root_causes: Mapped[list["CorrectionEventRootCause"]] = relationship(back_populates="correction_event")
    tie_groups: Mapped[list["CorrectionEventTieGroup"]] = relationship(back_populates="correction_event")
    candidate_states: Mapped[list["CorrectionEventCandidateState"]] = relationship(
        back_populates="correction_event", foreign_keys="CorrectionEventCandidateState.correction_event_id"
    )
    impact_scope_elements: Mapped[list["CorrectionEventImpactScopeElement"]] = relationship(back_populates="correction_event")
    accounting_corrections: Mapped[list["CorrectionEventAccountingCorrection"]] = relationship(back_populates="correction_event")


class CorrectionEventRootCause(Base):
    """Root Cause = حركة جديدة واحدة، أو (reversal + replacement) مرتبطان —
    لا يُفترَض أبداً أن الجذر InventoryMovement واحد (راجع Q3.1)."""
    __tablename__ = "correction_event_root_causes"

    id: Mapped[int] = mapped_column(primary_key=True)
    correction_event_id: Mapped[int] = mapped_column(ForeignKey("correction_events.id"), nullable=False, index=True)
    component_type: Mapped[RootCauseComponentType] = mapped_column(Enum(RootCauseComponentType))
    # نفس نمط source_type/source_id الموجود فعلاً في InventoryMovement/JournalEntry
    # — لا FK حقيقي، لأن المرجع قد يكون حركة أو مستنداً (شراء/عكس/فاتورة بديلة).
    source_type: Mapped[str] = mapped_column(String(30), index=True)
    source_id: Mapped[int | None] = mapped_column(Integer, index=True)

    correction_event: Mapped["CorrectionEvent"] = relationship(back_populates="root_causes")


class CorrectionEventTieGroup(Base):
    """مجموعة حركات بنفس movement_date لا يحسم ترتيبها الاقتصادي الفعلي —
    راجع Q2.5. صفر مجموعات يعني تسلسلاً زمنياً معروفاً بالكامل لهذا الحدث."""
    __tablename__ = "correction_event_tie_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    correction_event_id: Mapped[int] = mapped_column(ForeignKey("correction_events.id"), nullable=False, index=True)
    tied_movement_date: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)

    correction_event: Mapped["CorrectionEvent"] = relationship(back_populates="tie_groups")
    ordering_assumptions: Mapped[list["CorrectionEventOrderingAssumption"]] = relationship(back_populates="tie_group")


class CorrectionEventOrderingAssumption(Base):
    """حل مفترَض واحد محدَّد لترتيب TieGroup — التمثيل المُختزَل (run-collapsed
    interleaving) من Q2.5، لا permutation خام. Assumed Costing Order صراحة،
    لا Historical Fact مكتشف."""
    __tablename__ = "correction_event_ordering_assumptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tie_group_id: Mapped[int] = mapped_column(ForeignKey("correction_event_tie_groups.id"), nullable=False, index=True)
    ordering_representation: Mapped[str] = mapped_column(Text)

    tie_group: Mapped["CorrectionEventTieGroup"] = relationship(back_populates="ordering_assumptions")


class CorrectionEventCandidateState(Base):
    """نتيجة تحليلية واحدة — ليست بالضرورة ناتجة عن غموض (1..N دائماً، راجع
    تصحيح الـcardinality في Q3.6). Delta-primary، Baseline بالإشارة لا بالنسخ
    (Q3.3.1)."""
    __tablename__ = "correction_event_candidate_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    correction_event_id: Mapped[int] = mapped_column(ForeignKey("correction_events.id"), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(10))  # "A"/"B"... للعرض فقط، ليست هوية
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    correction_event: Mapped["CorrectionEvent"] = relationship(
        back_populates="candidate_states", foreign_keys=[correction_event_id]
    )
    ordering_links: Mapped[list["CorrectionEventCandidateOrderingLink"]] = relationship(back_populates="candidate_state")
    delta_records: Mapped[list["CorrectionEventDeltaRecord"]] = relationship(back_populates="candidate_state")


class CorrectionEventCandidateOrderingLink(Base):
    """يربط Candidate بمجموعة الافتراضات (واحد لكل TieGroup) التي أنتجته معاً
    — يجب أن يمثل كل Candidate حلاً متسقاً عالمياً (Q3.3.7)، لا اختياراً محلياً.
    tie_group_id مُكرَّر عمداً لتمكين القيد أدناه: لا يجوز لنفس الـCandidate أن
    يحمل افتراضين متناقضين لنفس مجموعة التعادل — يُرفَض على مستوى DB، لا
    Python فقط."""
    __tablename__ = "correction_event_candidate_ordering_links"
    __table_args__ = (
        UniqueConstraint(
            "candidate_state_id", "tie_group_id",
            name="uq_candidate_one_assumption_per_tiegroup",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_state_id: Mapped[int] = mapped_column(
        ForeignKey("correction_event_candidate_states.id"), nullable=False, index=True
    )
    ordering_assumption_id: Mapped[int] = mapped_column(
        ForeignKey("correction_event_ordering_assumptions.id"), nullable=False
    )
    tie_group_id: Mapped[int] = mapped_column(ForeignKey("correction_event_tie_groups.id"), nullable=False)

    candidate_state: Mapped["CorrectionEventCandidateState"] = relationship(back_populates="ordering_links")


class CorrectionEventDeltaRecord(Base):
    """المتغيران البدائيان فقط (Q3.3.4) — quantity وvalue؛ average_cost وما
    يُشتَق منهما (movement_unit_cost، cogs_consequence) محسوبان عند الطلب،
    غير مخزَّنين هنا. delta_quantity قد تكون NULL على مستوى الحركة الفردية إذا
    مُثِّلت على مستوى regime-segment بدلاً من ذلك (راجع الملاحظة في Q3.3.4 عن
    ثبات ΔQuantity داخل نطاق المستودع)."""
    __tablename__ = "correction_event_delta_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_state_id: Mapped[int] = mapped_column(
        ForeignKey("correction_event_candidate_states.id"), nullable=False, index=True
    )
    movement_id: Mapped[int] = mapped_column(ForeignKey("inventory_movements.id"), nullable=False)
    delta_quantity: Mapped[object | None] = mapped_column(Numeric(14, 3))
    delta_inventory_value: Mapped[object] = mapped_column(Numeric(14, 4))

    candidate_state: Mapped["CorrectionEventCandidateState"] = relationship(back_populates="delta_records")


class CorrectionEventImpactScopeElement(Base):
    """عقدة واحدة في رسم Q2.6 — حركة، مستند، أو أثر محاسبي. UNIQUE يمنع تكرار
    نفس العنصر داخل نطاق نفس الحدث."""
    __tablename__ = "correction_event_impact_scope_elements"
    __table_args__ = (
        UniqueConstraint(
            "correction_event_id", "source_type", "source_id",
            name="uq_scope_element_no_duplicate",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    correction_event_id: Mapped[int] = mapped_column(ForeignKey("correction_events.id"), nullable=False, index=True)
    kind: Mapped[ImpactScopeElementKind] = mapped_column(Enum(ImpactScopeElementKind))
    source_type: Mapped[str] = mapped_column(String(30), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)

    correction_event: Mapped["CorrectionEvent"] = relationship(back_populates="impact_scope_elements")


class CorrectionEventImpactScopeRelationship(Base):
    """حافة واحدة في رسم Q2.6 — قد تعبر مستودعات، وقد تُشكِّل دورة (W1→W2→W1،
    مثبتة في Q2.4) — عقد الرسم حركات فعلية منتهية العدد دائماً، فلا مشكلة
    إنهاء، فقط مشكلة حجم (Q2.7)."""
    __tablename__ = "correction_event_impact_scope_relationships"

    id: Mapped[int] = mapped_column(primary_key=True)
    correction_event_id: Mapped[int] = mapped_column(ForeignKey("correction_events.id"), nullable=False, index=True)
    from_element_id: Mapped[int] = mapped_column(
        ForeignKey("correction_event_impact_scope_elements.id"), nullable=False
    )
    to_element_id: Mapped[int] = mapped_column(
        ForeignKey("correction_event_impact_scope_elements.id"), nullable=False
    )
    relationship_type: Mapped[ImpactScopeRelationshipType] = mapped_column(Enum(ImpactScopeRelationshipType))


class CorrectionEventAccountingCorrection(Base):
    """مرجع خفيف فقط إلى JournalEntry الحقيقي المُنشأ عبر post_immediate() —
    ليس دفتراً محاسبياً موازياً. نفس نمط Settlement.journal_entry_id /
    OpeningBalance الموجود فعلاً في المشروع (unique=True: قيد واحد لكل
    تصحيح)."""
    __tablename__ = "correction_event_accounting_corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    correction_event_id: Mapped[int] = mapped_column(ForeignKey("correction_events.id"), nullable=False, index=True)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"), unique=True)

    correction_event: Mapped["CorrectionEvent"] = relationship(back_populates="accounting_corrections")


# ---------------------------------------------------------------------------
# محرك إنشاء قاعدة بيانات جديدة لعميل
# ---------------------------------------------------------------------------

def create_company_database(db_path: str) -> None:
    """ينشئ ملف SQLite جديد بكامل الجداول لعميل جديد."""
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    # اختبار سريع
    create_company_database("test_client.db")
    print("تم إنشاء قاعدة بيانات تجريبية: test_client.db")
