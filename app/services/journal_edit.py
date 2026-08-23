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


def _validate_lines(entry: JournalEntry) -> None:
    """قواعد صارمة على كل سطر — تُفرض هنا بالخدمة، لا تعتمد على الواجهة إطلاقاً:
    - لازم حساب محدد
    - لازم مبلغ واحد فقط (مدين أو دائن)، مو الاثنين معاً، ومو صفر بالاثنين"""
    errors: list[str] = []
    for i, line in enumerate(entry.lines, start=1):
        if not line.account_id:
            errors.append(f"السطر {i}: لم يُحدَّد حساب")
            continue
        d, c = money(line.debit), money(line.credit)
        if d > 0 and c > 0:
            errors.append(f"السطر {i}: لا يجوز أن يحتوي مبلغاً مديناً ودائناً معاً بنفس السطر")
        elif d == 0 and c == 0:
            errors.append(f"السطر {i}: لازم مبلغ مدين أو دائن — لا يجوز سطر فارغ")
    if errors:
        raise JournalEditError(" — ".join(errors))


def add_manual_line(session: Session, entry: JournalEntry, account_id: int,
                     debit=0, credit=0, exchange_rate=1, cost_center: str | None = None) -> JournalLine:
    ensure_editable(entry)
    if not account_id:
        raise JournalEditError("لا يمكن إضافة سطر بدون تحديد حساب")
    d, c = money(debit), money(credit)
    if d > 0 and c > 0:
        raise JournalEditError("لا يجوز أن يحتوي السطر مبلغاً مديناً ودائناً معاً — اختر واحداً فقط")
    if d == 0 and c == 0:
        raise JournalEditError("لازم إدخال مبلغ مدين أو دائن — لا يجوز سطر فارغ")
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
    """يقفل القيد نهائياً بعد التأكد من صحته الكاملة — لا رجعة بعدها إلا بالعكس."""
    ensure_editable(entry)
    if not entry.lines:
        raise JournalEditError("القيد بدون أسطر — لا يمكن ترحيله")
    _validate_lines(entry)
    if not entry.is_balanced():
        diff = sum(money(l.debit) for l in entry.lines) - sum(money(l.credit) for l in entry.lines)
        raise JournalEditError(
            f"القيد {entry.ref_no} غير متوازن (الفرق = {diff}) — لا يُرحّل حتى يتوازن تماماً"
        )
    entry.status = JournalEntryStatus.POSTED
    session.flush()
    return entry
