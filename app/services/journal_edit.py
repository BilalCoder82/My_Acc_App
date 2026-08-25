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
from app.services.money import money, rate as rate_


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


def reverse_manual_entry(session: Session, original_entry: JournalEntry,
                          reversal_date, description: str | None = None) -> JournalEntry:
    """يعكس قيداً يدوياً مرحّلاً — نفس مبدأ post_return للفواتير بالضبط
    (عكس دقيق، لا إعادة حساب). كل سطر يعكس مدين/دائن بنفس العملة وسعر
    الصرف وقيمة التعادل الأساسي الأصليين تماماً — القيد المعكوس لا يُعاد
    تسعيره بسعر صرف اليوم إطلاقاً، لأن سعر الصرف المحفوظ لقطة تاريخية
    ثابتة (نفس مبدأ QuickBooks Snapshot Exchange Rate).

    قاعدتان إضافيتان صارمتان:
    - لا يجوز عكس قيد هو نفسه عكس لقيد آخر (لا نعكس العكوس)
    - لا يجوز عكس نفس القيد الأصلي مرتين (عكس واحد فقط لكل قيد)
    """
    if original_entry.status != JournalEntryStatus.POSTED:
        raise JournalEditError(f"القيد {original_entry.ref_no} غير مرحّل — لا يوجد ما يُعكس")
    if original_entry.is_reversal_of is not None:
        raise JournalEditError(
            f"القيد {original_entry.ref_no} هو نفسه قيد عكسي — لا يجوز عكس قيد عكسي. "
            "لعكس الأثر، أنشئ قيداً تصحيحياً جديداً بدلاً من ذلك."
        )
    existing_reversal = session.query(JournalEntry).filter_by(
        is_reversal_of=original_entry.id
    ).first()
    if existing_reversal is not None:
        raise JournalEditError(
            f"القيد {original_entry.ref_no} مُعكوس أصلاً بالقيد {existing_reversal.ref_no} — "
            "لا يجوز عكسه مرة ثانية."
        )

    count = session.query(JournalEntry).filter(JournalEntry.ref_no.like("JV-REV-%")).count()
    reversal = JournalEntry(
        entry_date=reversal_date,
        ref_no=f"JV-REV-{count + 1:06d}",
        description=description or f"عكس القيد {original_entry.ref_no}",
        source_type="manual_reversal", is_reversal_of=original_entry.id,
        currency_code=original_entry.currency_code, exchange_rate=original_entry.exchange_rate,
        status=JournalEntryStatus.POSTED,
    )
    reversal.lines = [
        JournalLine(
            account_id=l.account_id, debit=l.credit, credit=l.debit,
            debit_base=l.credit_base, credit_base=l.debit_base,
            line_currency_code=l.line_currency_code, line_exchange_rate=l.line_exchange_rate,
            cost_center=l.cost_center,
        )
        for l in original_entry.lines
    ]
    if not reversal.is_balanced():
        raise JournalEditError("خطأ داخلي: قيد العكس غير متوازن — لا يُرحّل")

    session.add(reversal)
    session.flush()
    return reversal


def add_manual_line(session: Session, entry: JournalEntry, account_id: int,
                     debit=0, credit=0, exchange_rate=1, cost_center: str | None = None,
                     line_currency_code: str | None = None, line_exchange_rate=None) -> JournalLine:
    """
    line_currency_code / line_exchange_rate: تُمرَّر فقط لو كان هذا السطر
    بعملة مختلفة عن عملة القيد الافتراضية (مثال: سطر بالدولار داخل قيد
    عملته الافتراضية ليرة سورية). لو تُركا None، السطر يرث عملة القيد
    وسعر الصرف الممرَّر بـ`exchange_rate` — وهذا هو القيد أحادي العملة
    الشائع، ولا شيء يتغيّر بسلوكه القديم.

    debit_base/credit_base تُحسب دائماً بسعر الصرف الفعلي لهذا السطر
    تحديداً (سعر السطر لو مُحدَّد، وإلا سعر القيد) — هذا ما يجعل التوازن
    بالعملة الأساسية صحيحاً حتى لو اختلفت عملة كل سطر عن التاني.
    """
    ensure_editable(entry)
    if not account_id:
        raise JournalEditError("لا يمكن إضافة سطر بدون تحديد حساب")
    d, c = money(debit), money(credit)
    if d > 0 and c > 0:
        raise JournalEditError("لا يجوز أن يحتوي السطر مبلغاً مديناً ودائناً معاً — اختر واحداً فقط")
    if d == 0 and c == 0:
        raise JournalEditError("لازم إدخال مبلغ مدين أو دائن — لا يجوز سطر فارغ")

    effective_rate = rate_(line_exchange_rate if line_exchange_rate is not None else exchange_rate)
    line = JournalLine(
        entry_id=entry.id, account_id=account_id, debit=d, credit=c,
        debit_base=money(d * effective_rate), credit_base=money(c * effective_rate),
        line_currency_code=line_currency_code, line_exchange_rate=line_exchange_rate,
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
        # الفرق المعروض دائماً بالعملة الأساسية (debit_base/credit_base) — طرح
        # مبالغ بعملات مختلفة (debit/credit الخام) مباشرة بلا معنى محاسبياً
        diff = sum(money(l.debit_base) for l in entry.lines) - sum(money(l.credit_base) for l in entry.lines)
        raise JournalEditError(
            f"القيد {entry.ref_no} غير متوازن بالعملة الأساسية (الفرق = {diff}) — لا يُرحّل حتى يتوازن تماماً"
        )
    entry.status = JournalEntryStatus.POSTED
    session.flush()
    return entry
