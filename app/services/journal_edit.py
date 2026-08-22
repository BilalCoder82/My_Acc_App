"""
Journal Entry Edit Rules — سند القيد اليدوي
================================================
نفس قاعدة invoice_edit.py بالضبط: قيد POSTED لا يُعدَّل ولا تُحذف أسطره
مباشرة. سند القيد اليدوي (بعكس قيود الفواتير الآلية) يُنشأ DRAFT ابتداءً،
ويحتاج استدعاء post_manual_entry() صراحة ليُقفل.
"""

from __future__ import annotations
from sqlalchemy.orm import Session

from app.models import JournalEntry, JournalLine, JournalEntryStatus
from app.services.money import money


class JournalEditError(Exception):
    pass


def ensure_editable(entry: JournalEntry) -> None:
    if entry.status != JournalEntryStatus.DRAFT:
        raise JournalEditError(
            f"القيد {entry.ref_no} بحالة {entry.status.value} — لا يمكن تعديله. "
            "القيود المرحّلة تُعكس فقط عبر post_return أو قيد عكسي يدوي جديد."
        )


def add_manual_line(session: Session, entry: JournalEntry, account_id: int,
                     debit=0, credit=0, exchange_rate=1, cost_center: str | None = None) -> JournalLine:
    ensure_editable(entry)
    d, c = money(debit), money(credit)
    line = JournalLine(
        entry_id=entry.id, account_id=account_id, debit=d, credit=c,
        debit_base=money(d * money(exchange_rate)), credit_base=money(c * money(exchange_rate)),
        cost_center=cost_center,
    )
    session.add(line)
    session.flush()
    return line


def remove_manual_line(session: Session, line: JournalLine) -> None:
    ensure_editable(line.entry)
    session.delete(line)
    session.flush()


def post_manual_entry(session: Session, entry: JournalEntry) -> JournalEntry:
    """يقفل القيد نهائياً بعد التأكد من توازنه — لا رجعة بعدها إلا بالعكس."""
    ensure_editable(entry)
    if not entry.lines:
        raise JournalEditError("القيد بدون أسطر — لا يمكن ترحيله")
    if not entry.is_balanced():
        raise JournalEditError(
            f"القيد {entry.ref_no} غير متوازن — الفرق موجود، لا يُرحّل حتى يتوازن تماماً"
        )
    entry.status = JournalEntryStatus.POSTED
    session.flush()
    return entry
