"""
Journal Entry Edit Rules — سند القيد اليدوي
================================================
نفس قاعدة invoice_edit.py بالضبط: قيد POSTED لا يُعدَّل ولا تُحذف أسطره
مباشرة. سند القيد اليدوي (بعكس قيود الفواتير الآلية) يُنشأ DRAFT ابتداءً،
ويحتاج استدعاء post_manual_entry() صراحة ليُقفل.
"""

from __future__ import annotations
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Account, JournalEntry, JournalLine, JournalEntryStatus
from app.services.money import money, rate as rate_


class JournalEditError(Exception):
    pass


# =============================================================================
# PHASE3B4 — Accounting Posting Boundary (PHASE3B4_DESIGN_SPEC.md §1-§5)
# =============================================================================
# ملاحظة نطاق (Anti-Scope-Creep): الدوال أدناه إضافية بجانب add_manual_line/
# post_manual_entry/reverse_manual_entry الحاليتين، لا تستبدلهما بعد —
# الدومينات غير المُهاجَرة (journal_voucher_form.py، posting.py، settlements.py،
# opening_inventory) ما زالت تستدعي الدوال القديمة كما هي بلا أي تغيير، حسب
# ترتيب الهجرة المُقفَل بـ§6. لا تُحذف الدوال القديمة قبل هجرة كل مستدعٍ لها.

@dataclass
class LineIntent:
    account_id: int
    debit_raw: Decimal = field(default_factory=lambda: Decimal("0"))
    credit_raw: Decimal = field(default_factory=lambda: Decimal("0"))
    debit_base: Decimal = field(default_factory=lambda: Decimal("0"))
    credit_base: Decimal = field(default_factory=lambda: Decimal("0"))
    line_currency_code: str | None = None
    line_exchange_rate: Decimal | None = None


@dataclass
class AccountingIntent:
    entry_date: date
    currency_code: str
    exchange_rate: Decimal
    source_type: str
    description: str
    lines: list[LineIntent] = field(default_factory=list)
    source_id: int | None = None


# namespace لكل source_type — PHASE3B4_DESIGN_SPEC.md §1. يُوسَّع فقط عند
# هجرة دومين جديد فعلياً؛ لا namespace يُضاف "احتياطاً" بلا دومين يستخدمه.
_NAMESPACE_BY_SOURCE_TYPE = {
    "opening_balance": "JV-OPEN",
}
_REVERSAL_NAMESPACE = "JV-REV"


def _namespace_for(source_type: str) -> str:
    try:
        return _NAMESPACE_BY_SOURCE_TYPE[source_type]
    except KeyError:
        raise JournalEditError(
            f"لا يوجد namespace مُسجَّل لـsource_type='{source_type}' — "
            "الدومين لم يُهاجَر بعد أو الـnamespace لم يُضَف صراحة بـ_NAMESPACE_BY_SOURCE_TYPE"
        )


def _reserve_ref_no(session: Session, namespace: str) -> str:
    """يحجز الرقم التالي بـnamespace مُعطى، بـTransaction مستقلة تماماً عن
    جلسة المستند (§1/§4 — قرار تصميمي ملزم، لا تفصيل مؤجَّل).

    قاعدة بيانات حقيقية (ملف SQLite — الحالة الوحيدة بالتطبيق الفعلي، كل
    عميل ملفه الخاص): اتصال sqlite3 خام منفصل تماماً عن SQLAlchemy Session
    الرئيسية. BEGIN IMMEDIATE يحجز قفل الكتابة فوراً، commit فوري بعده، ثم
    إغلاق الاتصال — لا صلة باقية بجلسة المستند إطلاقاً (الخيار الأكثر أماناً
    الموثَّق بـ§4 لتفادي تثبيت تغييرات أخرى معلَّقة عرَضياً على تلك الجلسة).

    قاعدة بيانات :memory: (**لا تحدث بالتطبيق الفعلي أبداً** — تحدث فقط
    بملفات الاختبار التي تستخدم sqlite:///:memory: للسرعة/العزل؛ راجع
    tests/test_opening_account_balances.py): اتصال ثانٍ منفصل مستحيل
    تقنياً هنا — كل اتصال :memory: قاعدة بيانات معزولة تماماً بذاتها، لا
    صلة لها بالأخرى حتى لنفس الرابط النصي. نستخدم بدلاً منها Session
    نفسها، مع **فرض صريح** ألا توجد أي تغييرات أخرى معلَّقة عليها في هذه
    اللحظة (يمنع بالضبط خطر "تثبيت تغييرات غير متعلقة عرَضياً" الموثَّق
    بـ§4 — لا نتجاوزه، فقط نتحقق منه صراحة بدل تركه صامتاً)، ثم commit
    فوري لهذه الخطوة فقط. هذا القيد (:memory: + جلسة نظيفة) غير موجود
    بالتطبيق الفعلي أصلاً، فلا يُقيِّد أي استخدام حقيقي — فقط اختبارات."""
    from sqlalchemy import text as _sql

    bind = session.get_bind()
    db_path = bind.url.database
    is_memory = not db_path or db_path == ":memory:" or "mode=memory" in str(db_path)

    if not is_memory:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO journal_number_sequences (namespace, last_value) VALUES (?, 0) "
                "ON CONFLICT(namespace) DO NOTHING",
                (namespace,),
            )
            conn.execute(
                "UPDATE journal_number_sequences SET last_value = last_value + 1 WHERE namespace = ?",
                (namespace,),
            )
            row = conn.execute(
                "SELECT last_value FROM journal_number_sequences WHERE namespace = ?", (namespace,)
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return f"{namespace}-{row[0]:06d}"

    if session.dirty or session.new or session.deleted:
        raise JournalEditError(
            "لا يمكن حجز ref_no على قاعدة :memory: والجلسة تحوي تغييرات معلَّقة أخرى — "
            "هذا يخالف صراحة القيد الموثَّق بـ§4 (لا commit مبكر يُثبِّت تغييرات غير متعلقة). "
            "غير وارد بالتطبيق الفعلي (ملف SQLite حقيقي لكل عميل دائماً) — قيد اختباري فقط."
        )
    session.execute(_sql(
        "INSERT INTO journal_number_sequences (namespace, last_value) VALUES (:ns, 0) "
        "ON CONFLICT(namespace) DO NOTHING"
    ), {"ns": namespace})
    session.execute(_sql(
        "UPDATE journal_number_sequences SET last_value = last_value + 1 WHERE namespace = :ns"
    ), {"ns": namespace})
    row = session.execute(_sql(
        "SELECT last_value FROM journal_number_sequences WHERE namespace = :ns"
    ), {"ns": namespace}).fetchone()
    session.commit()
    return f"{namespace}-{row[0]:06d}"


def _validate_line(session: Session, account_id: int, debit_raw, credit_raw, debit_base, credit_base,
                    line_currency_code: str | None, line_exchange_rate) -> None:
    """§5 بالمواصفة، الخطوات 1-7 بالترتيب حرفياً. لا تتحقق أبداً من
    debit_base == debit_raw × rate — فقط الخصائص المذكورة صراحة."""
    account = session.get(Account, account_id)
    if account is None:
        raise JournalEditError(f"حساب غير موجود (id={account_id})")
    if not account.is_active:
        raise JournalEditError(f"الحساب ({account.name_ar}) غير نشط — لا يقبل قيوداً جديدة")
    if account.is_group:
        raise JournalEditError(f"الحساب ({account.name_ar}) تجميعي (Group) — لا يقبل قيوداً مباشرة")

    dr, cr = money(debit_raw), money(credit_raw)
    if dr > 0 and cr > 0:
        raise JournalEditError("لا يجوز أن يحتوي السطر مبلغاً مديناً ودائناً معاً — اختر واحداً فقط")
    if dr == 0 and cr == 0:
        raise JournalEditError("لازم إدخال مبلغ مدين أو دائن — لا يجوز سطر فارغ (0,0)")

    for label, val in (("debit_raw", debit_raw), ("credit_raw", credit_raw),
                        ("debit_base", debit_base), ("credit_base", credit_base)):
        if money(val) < 0:
            raise JournalEditError(f"{label} لا يجوز أن يكون سالباً (القيمة: {val})")

    if line_exchange_rate is not None and rate_(line_exchange_rate) <= 0:
        raise JournalEditError(f"line_exchange_rate يجب أن يكون أكبر من صفر إن وُجد (القيمة: {line_exchange_rate})")


def _validate_entry_balance(entry: JournalEntry) -> None:
    if not entry.lines:
        raise JournalEditError(f"القيد {entry.ref_no} بدون أسطر — لا يمكن ترحيله")
    total_debit_base = sum(money(l.debit_base) for l in entry.lines)
    total_credit_base = sum(money(l.credit_base) for l in entry.lines)
    if total_debit_base != total_credit_base:
        diff = total_debit_base - total_credit_base
        raise JournalEditError(
            f"القيد {entry.ref_no} غير متوازن بالعملة الأساسية (الفرق = {diff}) — لا يُرحّل حتى يتوازن تماماً"
        )


def begin_entry(session: Session, entry_date: date, currency_code: str, exchange_rate,
                 source_type: str, description: str) -> JournalEntry:
    """نمط أ — DRAFT. يحجز ref_no فوراً (خطوة أولى، قبل أي شيء آخر، §4)."""
    ref_no = _reserve_ref_no(session, _namespace_for(source_type))
    entry = JournalEntry(
        entry_date=entry_date, ref_no=ref_no, description=description,
        source_type=source_type, currency_code=currency_code, exchange_rate=rate_(exchange_rate),
        status=JournalEntryStatus.DRAFT,
    )
    session.add(entry)
    session.flush()
    return entry


def add_line(session: Session, entry: JournalEntry, account_id: int,
             debit_raw=0, credit_raw=0, debit_base=0, credit_base=0,
             line_currency_code: str | None = None, line_exchange_rate=None) -> JournalLine:
    ensure_editable(entry)
    _validate_line(session, account_id, debit_raw, credit_raw, debit_base, credit_base,
                    line_currency_code, line_exchange_rate)
    line = JournalLine(
        entry_id=entry.id, account_id=account_id,
        debit=money(debit_raw), credit=money(credit_raw),
        debit_base=money(debit_base), credit_base=money(credit_base),
        line_currency_code=line_currency_code, line_exchange_rate=line_exchange_rate,
    )
    session.add(line)
    session.flush()
    return line


def remove_line(session: Session, line: JournalLine) -> None:
    ensure_editable(line.entry)
    session.delete(line)
    session.flush()


def post(session: Session, entry: JournalEntry) -> JournalEntry:
    ensure_editable(entry)
    _validate_entry_balance(entry)
    entry.status = JournalEntryStatus.POSTED
    session.flush()
    return entry


def post_immediate(session: Session, intent: AccountingIntent) -> JournalEntry:
    """نمط ب — POSTED مباشرة. نفس دالتَي التحقق حرفياً، لا نسخة ثانية."""
    ref_no = _reserve_ref_no(session, _namespace_for(intent.source_type))
    entry = JournalEntry(
        entry_date=intent.entry_date, ref_no=ref_no, description=intent.description,
        source_type=intent.source_type, source_id=intent.source_id,
        currency_code=intent.currency_code, exchange_rate=rate_(intent.exchange_rate),
        status=JournalEntryStatus.DRAFT,  # يُثبَّت POSTED فقط بعد نجاح كل التحققات أدناه
    )
    session.add(entry)
    session.flush()

    for li in intent.lines:
        _validate_line(session, li.account_id, li.debit_raw, li.credit_raw, li.debit_base, li.credit_base,
                        li.line_currency_code, li.line_exchange_rate)
        session.add(JournalLine(
            entry_id=entry.id, account_id=li.account_id,
            debit=money(li.debit_raw), credit=money(li.credit_raw),
            debit_base=money(li.debit_base), credit_base=money(li.credit_base),
            line_currency_code=li.line_currency_code, line_exchange_rate=li.line_exchange_rate,
        ))
    session.flush()
    session.expire(entry, ["lines"])  # يفرض تحميلاً نظيفاً يلتقط كل الأسطر المُضافة أعلاه فعلياً
    _validate_entry_balance(entry)
    entry.status = JournalEntryStatus.POSTED
    session.flush()
    return entry


def reverse(session: Session, original_entry: JournalEntry, reversal_date: date,
            description: str | None = None) -> JournalEntry:
    """العكس العام (§3 بالمواصفة). **تحذير نطاق مسجَّل صراحة (لا يُصلَح هنا)**:
    يحجز الرقم من namespace "JV-REV" عبر Sequence Table — لكن reverse_manual_entry()
    القديمة أدناه (لا تزال مُستخدَمة فعلياً من journal_voucher_form.py وopening_balances.py
    الحاليين، لم يُهاجَرا بعد) تحجز من *نفس* الـprefix "JV-REV-%" بآلية LIKE/COUNT
    منفصلة تماماً لا تعرف بوجود هذا الـSequence. استخدام الدالتين معاً بنفس الوقت على
    قاعدة بيانات فيها بيانات JV-REV قديمة **سيُنتِج تصادم ref_no فعلياً** (UNIQUE
    constraint على JournalEntry.ref_no) لأن الـSequence الجديدة تبدأ من last_value=0
    بلا علم بعدد صفوف JV-REV-% الموجودة أصلاً. لذلك: هذه الدالة **لا تُستدعى من أي
    Domain مُهاجَر بعد** (opening_balances.py لا يزال يستخدم reverse_manual_entry
    القديمة عمداً بهذه الدفعة) — الانتقال الآمن لـ"JV-REV" يحتاج قراراً منفصلاً صريحاً
    (Backfill/seed للـSequence بالقيمة الحالية الفعلية، أو تجميد كل مستدعي
    reverse_manual_entry دفعة واحدة). موثَّق كـTechnical Debt بالتقرير، غير محلول عمداً
    بهذه الدفعة."""
    if original_entry.status != JournalEntryStatus.POSTED:
        raise JournalEditError(f"القيد {original_entry.ref_no} غير مرحّل — لا يوجد ما يُعكس")
    if original_entry.is_reversal_of is not None:
        raise JournalEditError(
            f"القيد {original_entry.ref_no} هو نفسه قيد عكسي — لا يجوز عكس قيد عكسي."
        )
    existing_reversal = session.query(JournalEntry).filter_by(is_reversal_of=original_entry.id).first()
    if existing_reversal is not None:
        raise JournalEditError(
            f"القيد {original_entry.ref_no} مُعكوس أصلاً بالقيد {existing_reversal.ref_no} — لا يجوز عكسه مرة ثانية."
        )
    if reversal_date < original_entry.entry_date:
        raise JournalEditError(
            f"تاريخ العكس ({reversal_date}) أسبق من تاريخ القيد الأصلي ({original_entry.entry_date}) — غير مسموح"
        )

    ref_no = _reserve_ref_no(session, _REVERSAL_NAMESPACE)
    reversal = JournalEntry(
        entry_date=reversal_date, ref_no=ref_no,
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
