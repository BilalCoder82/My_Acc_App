"""
Default Chart of Accounts Template
=====================================
شجرة حسابات قياسية لشركة/مطعم صغير. تُستدعى مرة واحدة عند إنشاء عميل
جديد. تنشئ الحسابات **وتربط الإعدادات الأساسية تلقائياً** (Setting)
حتى تشتغل الفواتير فوراً بدون أي إعداد يدوي إضافي.

لا تُستدعى هذه الدالة مرتين على نفس قاعدة البيانات — تتحقق من عدم
وجود حسابات مسبقاً وترفض لو وُجدت، لتفادي تكرار الشجرة بالغلط.
"""

from __future__ import annotations
from sqlalchemy.orm import Session

from app.models import Account, AccountType, AccountSubtype, Setting

def create_default_chart_of_accounts(session: Session) -> dict[str, Account]:
    if session.query(Account).count() > 0:
        raise ValueError(
            "يوجد حسابات مسبقاً بهذه القاعدة — لا يمكن إنشاء الشجرة الافتراضية مرتين. "
            "لو تحتاج البدء من جديد، احذف الحسابات يدوياً أولاً."
        )

    def acc(code, name, atype, parent=None, is_group=False, subtype=AccountSubtype.GENERAL):
        a = Account(code=code, name_ar=name, account_type=atype, parent_id=parent.id if parent else None,
                     is_group=is_group, subtype=subtype)
        session.add(a)
        session.flush()
        return a

    # 1 الأصول
    assets = acc("1", "الأصول", AccountType.ASSET, is_group=True)
    current_assets = acc("11", "الأصول المتداولة", AccountType.ASSET, assets, is_group=True)
    cash = acc("1101", "الصندوق", AccountType.ASSET, current_assets, subtype=AccountSubtype.CASH)
    bank = acc("1102", "البنك", AccountType.ASSET, current_assets, subtype=AccountSubtype.BANK)
    # ar_parent/ap_parent مجموعتان أب فقط (is_group) — الحسابات الفعلية
    # القابلة للتسوية هي الحسابات الفرعية لكل عميل/مورد التي تُنشَأ
    # تلقائياً بـget_or_create_party_account (subtype=CUSTOMER/SUPPLIER
    # وallow_reconciliation=True هناك تحديداً، لا هنا).
    ar_parent = acc("1103", "الذمم المدينة", AccountType.ASSET, current_assets, is_group=True)
    inventory = acc("1104", "المخزون", AccountType.ASSET, current_assets)
    fixed_assets = acc("12", "الأصول الثابتة", AccountType.ASSET, assets, is_group=True)
    acc("1201", "أثاث ومعدات", AccountType.ASSET, fixed_assets)

    # 2 الالتزامات
    liabilities = acc("2", "الالتزامات", AccountType.LIABILITY, is_group=True)
    current_liabilities = acc("21", "الالتزامات المتداولة", AccountType.LIABILITY, liabilities, is_group=True)
    ap_parent = acc("2101", "الذمم الدائنة", AccountType.LIABILITY, current_liabilities, is_group=True)
    sales_tax = acc("2102", "ضريبة مبيعات مستحقة", AccountType.LIABILITY, current_liabilities)
    purchases_tax = acc("2103", "ضريبة مشتريات قابلة للخصم", AccountType.LIABILITY, current_liabilities)

    # 3 حقوق الملكية
    equity = acc("3", "حقوق الملكية", AccountType.EQUITY, is_group=True)
    acc("3101", "رأس المال", AccountType.EQUITY, equity)
    acc("3102", "الأرباح المرحّلة", AccountType.EQUITY, equity)

    # 4 الإيرادات
    revenue = acc("4", "الإيرادات", AccountType.REVENUE, is_group=True)
    sales = acc("4101", "المبيعات", AccountType.REVENUE, revenue, subtype=AccountSubtype.INCOME)
    acc("4102", "مردودات ومسموحات المبيعات", AccountType.REVENUE, revenue)
    fx_gain = acc("4103", "أرباح فروقات صرف", AccountType.REVENUE, revenue, subtype=AccountSubtype.OTHER)

    # 5 تكلفة المبيعات
    cost_group = acc("5", "تكلفة المبيعات", AccountType.EXPENSE, is_group=True)
    cogs = acc("5101", "كلفة البضاعة المباعة", AccountType.EXPENSE, cost_group, subtype=AccountSubtype.EXPENSE)

    # 6 المصروفات التشغيلية
    expenses = acc("6", "المصروفات", AccountType.EXPENSE, is_group=True)
    acc("6101", "رواتب وأجور", AccountType.EXPENSE, expenses, subtype=AccountSubtype.EXPENSE)
    acc("6102", "إيجار", AccountType.EXPENSE, expenses, subtype=AccountSubtype.EXPENSE)
    acc("6103", "كهرباء وماء", AccountType.EXPENSE, expenses, subtype=AccountSubtype.EXPENSE)
    acc("6104", "مصروفات متنوعة", AccountType.EXPENSE, expenses, subtype=AccountSubtype.EXPENSE)
    # حساب منفصل عن fx_gain عمداً (لا حساب مشترك) — راجع WORKFLOW.md §42.3
    # الكود 6106 وليس 6105 عمداً — 6105 مُستخدَم يدوياً بحساب فروقات صرف
    # مؤقت داخل tests/test_e2e_scenario.py (أُنشئ يدوياً قبل وجود هذه
    # الشجرة الافتراضية أصلاً)، لتفادي تصادم UNIQUE constraint.
    fx_loss = acc("6106", "خسائر فروقات صرف", AccountType.EXPENSE, expenses, subtype=AccountSubtype.OTHER)

    # ربط الإعدادات تلقائياً — الفواتير تشتغل فوراً بدون إعداد يدوي إضافي
    session.add_all([
        Setting(key="default_cash_account_id", value=str(cash.id)),
        Setting(key="default_sales_account_id", value=str(sales.id)),
        Setting(key="default_sales_tax_account_id", value=str(sales_tax.id)),
        Setting(key="default_purchases_tax_account_id", value=str(purchases_tax.id)),
        Setting(key="ar_parent_account_id", value=str(ar_parent.id)),
        Setting(key="ap_parent_account_id", value=str(ap_parent.id)),
        Setting(key="default_inventory_account_id", value=str(inventory.id)),
        Setting(key="default_cogs_account_id", value=str(cogs.id)),
        Setting(key="default_fx_gain_account_id", value=str(fx_gain.id)),
        Setting(key="default_fx_loss_account_id", value=str(fx_loss.id)),
    ])
    session.commit()

    return {
        "cash": cash, "bank": bank, "ar_parent": ar_parent, "inventory": inventory,
        "ap_parent": ap_parent, "sales_tax": sales_tax, "purchases_tax": purchases_tax,
        "sales": sales, "cogs": cogs, "fx_gain": fx_gain, "fx_loss": fx_loss,
    }
