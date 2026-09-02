"""
Party Sub-Accounts
====================
بدل جدول Customer/Supplier منفصل، ننشئ تلقائياً حساباً فرعياً تحت
"الذمم المدينة" (للعملاء) أو "الذمم الدائنة" (للموردين) عند أول ظهور
لاسم الطرف بفاتورة. هذا يعطي كشف حساب دقيق لكل طرف من دليل الحسابات
مباشرة، دون هجرة بيانات لاحقة إذا احتجنا جدول أطراف كامل يوماً ما.

الإعدادات المطلوبة بجدول Settings:
    ar_parent_account_id  -> الحساب الأب "الذمم المدينة" (is_group=True)
    ap_parent_account_id  -> الحساب الأب "الذمم الدائنة" (is_group=True)
"""

from __future__ import annotations
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import Account, AccountSubtype, Setting


def _get_setting_int(session: Session, key: str) -> int:
    row = session.get(Setting, key)
    if row is None:
        raise ValueError(f"إعداد مفقود: {key} — يجب تحديد الحساب الأب أولاً من شاشة الإعدادات")
    return int(row.value)


def get_or_create_party_account(session: Session, party_name: str, is_customer: bool) -> Account:
    """يرجّع الحساب الفرعي للطرف، وينشئه تلقائياً إن لم يكن موجوداً."""
    party_name = party_name.strip()
    if not party_name:
        raise ValueError("اسم الطرف فارغ — لا يمكن إنشاء حساب فرعي")

    parent_key = "ar_parent_account_id" if is_customer else "ap_parent_account_id"
    parent_id = _get_setting_int(session, parent_key)
    parent = session.get(Account, parent_id)
    if parent is None:
        raise ValueError(f"الحساب الأب المحدد بالإعدادات (id={parent_id}) غير موجود")

    # البحث عن حساب فرعي موجود بنفس الاسم تحت نفس الأب (لا نكرر إنشاء الحساب)
    existing = session.execute(
        select(Account).where(
            Account.parent_id == parent_id,
            Account.name_ar == party_name,
        )
    ).scalar_one_or_none()
    if existing:
        return existing

    # توليد كود فرعي تسلسلي: كود الأب + رقم تسلسلي (مثال: 1103-001)
    siblings_count = session.execute(
        select(Account).where(Account.parent_id == parent_id)
    ).scalars().all()
    next_seq = len(siblings_count) + 1
    new_code = f"{parent.code}-{next_seq:03d}"

    new_account = Account(
        code=new_code,
        name_ar=party_name,
        account_type=parent.account_type,
        parent_id=parent_id,
        currency_code=parent.currency_code,
        is_group=False,
        is_active=True,
        # §56: كل حساب طرف يُنشَأ تلقائياً هنا يُصنَّف فعلياً ويُسمَح له
        # بالتسوية بشكل صريح — لا اعتماداً على account_type أو رقم
        # الحساب لاحقاً بأي مكان (قرار Bilal الصريح).
        subtype=AccountSubtype.CUSTOMER if is_customer else AccountSubtype.SUPPLIER,
        allow_reconciliation=True,
    )
    session.add(new_account)
    session.flush()  # لنحصل على id قبل استخدامه بالقيد مباشرة
    return new_account
