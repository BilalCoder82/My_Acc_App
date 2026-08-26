"""
Account Edit Service — دليل الحسابات: إنشاء/تعديل/تفعيل
============================================================
كل القواعد المحاسبية والبنيوية هنا، لا بالواجهة — نفس مبدأ journal_edit.py.
الواجهة (account_card_dialog.py) لا تلمس session.add/commit أو تتحقق من
شيء بنفسها؛ فقط تجمع القيم وتستدعي هذه الدوال.

القواعد المطبَّقة (متفَق عليها بالنقاش قبل البناء):
- الحساب الأب (لو مُحدَّد) يجب أن يكون موجوداً فعلاً
- الكود لا يتكرر (بعد strip، بمقارنة حساسة لحالة الأحرف كما بالجدول)
- لا يجوز إنشاء دورة بالشجرة (حساب يصبح أباً لأحد أجداده)
- حساب له حركات مرحّلة مباشرة (JournalLine) لا يجوز تحويله لحساب تجميعي
  ولا تغيير نوعه (account_type) — كلاهما يُفسِدان صحة كشف الحساب والتقارير
  التاريخية القائمة على هذين الحقلين تحديداً
- لا حذف بهذا الإصدار إطلاقاً (v1) — فقط تفعيل/تعطيل (set_account_active)،
  وهو غير مقيَّد بأي شرط لأنه آمن تماماً بطبيعته (لا يمسّ بيانات تاريخية)
"""

from __future__ import annotations
from sqlalchemy.orm import Session

from app.models import Account, AccountType

VALID_CURRENCIES = {"SYP", "USD", "TRY", "EUR"}


class AccountEditError(Exception):
    pass


def _ancestor_ids(session: Session, account: Account) -> set[int]:
    """كل أسلاف الحساب (أبوه، جدّه...) — تُستخدم لمنع اختيار أحدهم كأب جديد
    (ذلك لا يُسبِّب دورة أصلاً)، والأهم: منع اختيار أحد الأحفاد كأب (دورة)."""
    ids: set[int] = set()
    current = account.parent
    while current is not None:
        ids.add(current.id)
        current = current.parent
    return ids


def _descendant_ids(session: Session, account: Account) -> set[int]:
    """كل أحفاد الحساب — اختيار أي منهم كأب جديد يُنشئ دورة بالشجرة."""
    ids: set[int] = set()
    children = session.query(Account).filter_by(parent_id=account.id).all()
    for child in children:
        ids.add(child.id)
        ids |= _descendant_ids(session, child)
    return ids


def _validate_common(
    session: Session, *, code: str, name_ar: str, account_type: AccountType,
    parent_id: int | None, currency_code: str, existing: Account | None,
) -> tuple[str, str, str]:
    code = (code or "").strip()
    name_ar = (name_ar or "").strip()
    currency_code = (currency_code or "SYP").strip().upper()

    if not code:
        raise AccountEditError("رمز الحساب مطلوب")
    if not name_ar:
        raise AccountEditError("اسم الحساب مطلوب")
    if account_type is None:
        raise AccountEditError("نوع الحساب مطلوب")
    if currency_code not in VALID_CURRENCIES:
        raise AccountEditError(f"عملة غير معروفة: {currency_code}")

    dup_query = session.query(Account).filter(Account.code == code)
    if existing is not None:
        dup_query = dup_query.filter(Account.id != existing.id)
    if dup_query.first() is not None:
        raise AccountEditError(f"رمز الحساب '{code}' مستخدم مسبقاً بحساب آخر")

    if parent_id is not None:
        parent = session.get(Account, parent_id)
        if parent is None:
            raise AccountEditError("الحساب الأب المحدَّد غير موجود")
        if existing is not None:
            if parent_id == existing.id:
                raise AccountEditError("لا يمكن أن يكون الحساب أباً لنفسه")
            if parent_id in _descendant_ids(session, existing):
                raise AccountEditError(
                    "لا يمكن اختيار أحد الحسابات الفرعية لهذا الحساب كأب له — هذا يُنشئ دورة بالشجرة"
                )

    return code, name_ar, currency_code


def create_account(
    session: Session, *, code: str, name_ar: str, account_type: AccountType,
    parent_id: int | None = None, currency_code: str = "SYP",
    is_group: bool = False, is_active: bool = True,
) -> Account:
    code, name_ar, currency_code = _validate_common(
        session, code=code, name_ar=name_ar, account_type=account_type,
        parent_id=parent_id, currency_code=currency_code, existing=None,
    )
    account = Account(
        code=code, name_ar=name_ar, account_type=account_type, parent_id=parent_id,
        currency_code=currency_code, is_group=is_group, is_active=is_active,
    )
    session.add(account)
    session.flush()
    return account


def update_account(
    session: Session, account: Account, *, code: str, name_ar: str,
    account_type: AccountType, parent_id: int | None, currency_code: str,
    is_group: bool, is_active: bool,
) -> Account:
    code, name_ar, currency_code = _validate_common(
        session, code=code, name_ar=name_ar, account_type=account_type,
        parent_id=parent_id, currency_code=currency_code, existing=account,
    )

    has_movements = len(account.lines) > 0
    if has_movements and account_type != account.account_type:
        raise AccountEditError(
            "لا يمكن تغيير نوع الحساب — له حركات مرحّلة مسبقاً بنوعه الحالي، "
            "وتغييره يُفسِد صحة كشف الحساب والتقارير التاريخية"
        )
    if has_movements and is_group and not account.is_group:
        raise AccountEditError(
            "لا يمكن تحويل هذا الحساب لحساب تجميعي — له حركات مرحّلة مباشرة عليه، "
            "والحساب التجميعي لا يجوز أن يُستخدم كحساب حركة"
        )

    account.code = code
    account.name_ar = name_ar
    account.account_type = account_type
    account.parent_id = parent_id
    account.currency_code = currency_code
    account.is_group = is_group
    account.is_active = is_active
    session.flush()
    return account


def set_account_active(session: Session, account: Account, is_active: bool) -> Account:
    """تفعيل/تعطيل — البديل الآمن للحذف. غير مقيَّد: التعطيل لا يمسّ أي بيانات
    تاريخية، فقط يُخفي الحساب من قوائم الاختيار الجديدة (list_postable_accounts
    يفلتر is_active بالفعل)."""
    account.is_active = is_active
    session.flush()
    return account
