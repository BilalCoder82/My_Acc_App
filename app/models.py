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
from datetime import datetime, date

from sqlalchemy import (
    create_engine, ForeignKey, String, Numeric, Date, DateTime,
    Boolean, Enum, Text, CheckConstraint, UniqueConstraint, Integer
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, Session
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

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    direction: Mapped[MovementDirection] = mapped_column(Enum(MovementDirection))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3))
    unit_cost: Mapped[float] = mapped_column(Numeric(14, 4))  # كلفة الوحدة وقت الحركة
    movement_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source_type: Mapped[str] = mapped_column(String(30))   # 'sales_invoice' / 'purchase_invoice' / 'opening_balance'
    source_id: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)

    item: Mapped["Item"] = relationship(back_populates="movements")


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
    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))
    kind: Mapped[str] = mapped_column(String(10))  # "receipt" | "payment"
    settlement_date: Mapped[date] = mapped_column(Date, default=date.today)
    # الجزء المُسوَّى من الفاتورة، بعملة الفاتورة نفسها (لا عملة القبض الفعلية)
    amount_foreign: Mapped[float] = mapped_column(Numeric(14, 2))
    settlement_rate: Mapped[float] = mapped_column(Numeric(14, 6))
    # فرق الصرف الناتج عن هذه التسوية تحديداً، بالعملة الأساسية.
    # موجب = ربح صرف، سالب = خسارة صرف (راجع WORKFLOW.md §42.3 للإشارة
    # حسب نوع الحساب: عميل مقابل مورد معكوسان).
    fx_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    invoice: Mapped["Invoice"] = relationship()
    journal_entry: Mapped["JournalEntry"] = relationship()


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
# الأرصدة الافتتاحية للحسابات (Phase 3B-1) — سجل تدقيق فقط، لا يحل محل
# JournalEntry/JournalLine (القيد الفعلي هو مصدر الحقيقة المحاسبية،
# هذا الجدول يحفظ المُدخَل الأصلي كما أدخله المستخدم لغرض العرض/التدقيق)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------

class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


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
