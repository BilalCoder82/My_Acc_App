"""يُستخدم من واجهة سند القيد للبحث السريع عن الحساب — حسابات فرعية فقط
(is_group=False)، لأن الحسابات التجميعية لا تقبل قيوداً مباشرة."""

from __future__ import annotations
from sqlalchemy.orm import Session
from app.models import Account


def list_postable_accounts(session: Session, search: str = "") -> list[Account]:
    query = session.query(Account).filter(Account.is_group == False, Account.is_active == True)  # noqa: E712
    if search:
        query = query.filter(
            (Account.code.ilike(f"%{search}%")) | (Account.name_ar.ilike(f"%{search}%"))
        )
    return query.order_by(Account.code).limit(30).all()
