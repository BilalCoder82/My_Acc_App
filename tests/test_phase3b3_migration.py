"""
tests/test_phase3b3_migration.py
===================================
اختبار Migration/Backfill فعلي لـPhase 3B-3 (PHASE3B3_DESIGN_SPEC.md §7)
— يبني قاعدة بيانات بالمخطط القديم فعلياً عبر Alembic نفسه (لا محاكاة)،
يُدخل بيانات Settlement قديمة حقيقية (invoice_id مباشر)، يُرقّي لأحدث
إصدار، ويتحقق من: (1) SettlementAllocation واحد بالضبط لكل Settlement
قديم، (2) currency_code/party_account_id امتلآ بشكل صحيح، (3) لا فقد
بيانات (المجاميع تتطابق)، (4) عمود invoice_id حُذف فعلياً من settlements،
(5) إعادة فتح فعلية (اتصال جديد تماماً) بعد الترقية.
"""
import os, sys, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic.config import Config
from alembic.command import upgrade

results = []


def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    results.append((name, cond))
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def alembic_cfg(db_path: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic_migrations")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
db_path = tmp.name

try:
    # === 1) بناء المخطط القديم فعلياً (قبل migration 3B-3) عبر Alembic ===
    cfg = alembic_cfg(db_path)
    upgrade(cfg, "c2d6b4a9f1e7")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")  # لإدخال بيانات اختبار مبسَّطة بسرعة

    # حسابات: عميل (AR) + مبيعات + صندوق
    conn.execute("INSERT INTO accounts (id, code, name_ar, account_type, currency_code, is_active, is_group, subtype, allow_reconciliation) VALUES (1, '1201', 'أحمد', 'ASSET', 'USD', 1, 0, 'CUSTOMER', 1)")
    conn.execute("INSERT INTO accounts (id, code, name_ar, account_type, currency_code, is_active, is_group) VALUES (2, '4001', 'المبيعات', 'REVENUE', 'USD', 1, 0)")
    conn.execute("INSERT INTO accounts (id, code, name_ar, account_type, currency_code, is_active, is_group) VALUES (3, '1001', 'الصندوق', 'ASSET', 'USD', 1, 0)")

    # قيد الفاتورة الأصلي: Dr AR 1000 / Cr Sales 1000
    conn.execute("INSERT INTO journal_entries (id, entry_date, ref_no, currency_code, exchange_rate, source_type, status, created_at) VALUES (1, '2026-01-01', 'INV-1', 'USD', 1, 'sales_invoice', 'POSTED', '2026-01-01')")
    conn.execute("INSERT INTO journal_lines (entry_id, account_id, debit, credit, debit_base, credit_base) VALUES (1, 1, 1000, 0, 1000, 0)")
    conn.execute("INSERT INTO journal_lines (entry_id, account_id, debit, credit, debit_base, credit_base) VALUES (1, 2, 0, 1000, 0, 1000)")

    # الفاتورة نفسها (لا نحتاج invoice_lines لهذا الاختبار — لا يستدعي compute_invoice_totals)
    conn.execute("INSERT INTO invoices (id, invoice_no, kind, invoice_date, party_name, currency_code, exchange_rate, status, discount_percent, discount_amount, journal_entry_id) VALUES (1, 'INV-1', 'sales', '2026-01-01', 'أحمد', 'USD', 1, 'posted', 0, 0, 1)")

    # قيد التسوية القديم: Dr Cash 400 / Cr AR 400
    conn.execute("INSERT INTO journal_entries (id, entry_date, ref_no, currency_code, exchange_rate, source_type, status, created_at) VALUES (2, '2026-01-15', 'JE-RCV-1', 'USD', 1, 'receipt', 'POSTED', '2026-01-15')")
    conn.execute("INSERT INTO journal_lines (entry_id, account_id, debit, credit, debit_base, credit_base) VALUES (2, 3, 400, 0, 400, 0)")
    conn.execute("INSERT INTO journal_lines (entry_id, account_id, debit, credit, debit_base, credit_base) VALUES (2, 1, 0, 400, 0, 400)")

    # صف Settlement القديم (بالمخطط القديم: invoice_id مباشر، بلا currency_code/party_account_id)
    conn.execute("INSERT INTO settlements (id, invoice_id, journal_entry_id, kind, settlement_date, amount_foreign, settlement_rate, fx_amount) VALUES (1, 1, 2, 'receipt', '2026-01-15', 400, 1, 0)")

    conn.commit()
    conn.close()

    # === 2) الترقية لأحدث إصدار (تطبيق migration 3B-3 فعلياً) ===
    cfg2 = alembic_cfg(db_path)
    upgrade(cfg2, "head")  # لو فشل التحقق الداخلي بالـmigration (RuntimeError)، الاختبار يفشل هنا مباشرة

    # === 3) إغلاق فعلي وإعادة فتح باتصال جديد تماماً (Reopen حقيقي) ===
    conn2 = sqlite3.connect(db_path)

    cols = [r[1] for r in conn2.execute("PRAGMA table_info(settlements)")]
    check("invoice_id حُذف فعلياً من settlements بعد الـmigration", "invoice_id" not in cols, str(cols))
    check("currency_code موجود بـsettlements", "currency_code" in cols)
    check("party_account_id موجود بـsettlements", "party_account_id" in cols)

    row = conn2.execute("SELECT currency_code, party_account_id FROM settlements WHERE id=1").fetchone()
    check("currency_code امتلأ صحيحاً من الفاتورة (USD)", row[0] == "USD", str(row))
    check("party_account_id امتلأ صحيحاً (حساب أحمد id=1)", row[1] == 1, str(row))

    alloc_rows = conn2.execute(
        "SELECT settlement_id, invoice_id, opening_party_entry_id, amount_foreign FROM settlement_allocations"
    ).fetchall()
    check("صف SettlementAllocation واحد بالضبط للـSettlement القديم", len(alloc_rows) == 1, str(alloc_rows))
    check("الـallocation يشير لنفس الفاتورة (invoice_id=1)", alloc_rows[0][1] == 1, str(alloc_rows))
    check("opening_party_entry_id فارغ (Exclusive Arc محفوظ)", alloc_rows[0][2] is None)
    check("مبلغ الـallocation يطابق مبلغ Settlement الأصلي (400) — لا فقد بيانات",
          float(alloc_rows[0][3]) == 400.0, str(alloc_rows))

    kind_type = next(r[2] for r in conn2.execute("PRAGMA table_info(settlements)") if r[1] == "kind")
    check("عمود kind أصبح String(20) (يستوعب customer_refund/supplier_refund)",
          "20" in kind_type, kind_type)

    # === 4) Hardening (§13/§10 بتعليمات Bilal): CHECK/UNIQUE يجب أن تعمل
    #        فعلياً عبر مسار Alembic أيضاً، لا فقط بقاعدة بيانات ORM طازجة ===
    import sqlite3 as _sqlite3
    try:
        conn2.execute(
            "INSERT INTO settlements (journal_entry_id, party_account_id, kind, settlement_date, "
            "currency_code, amount_foreign, settlement_rate, fx_amount) "
            "VALUES (2, 1, 'receipt', '2026-02-01', 'USD', -100, 1, 0)"
        )
        check("DB (Alembic path) يرفض Settlement.amount_foreign سالب", False, "لم يُرفَض!")
    except _sqlite3.IntegrityError:
        check("DB (Alembic path) يرفض Settlement.amount_foreign سالب", True)

    try:
        conn2.execute(
            "INSERT INTO settlements (journal_entry_id, party_account_id, kind, settlement_date, "
            "currency_code, amount_foreign, settlement_rate, fx_amount) "
            "VALUES (2, 1, 'receipt', '2026-02-01', 'USD', 100, 1, 0)"
        )
        check("DB (Alembic path) يرفض journal_entry_id مكرر بـSettlement (UNIQUE)", False, "لم يُرفَض!")
    except _sqlite3.IntegrityError:
        check("DB (Alembic path) يرفض journal_entry_id مكرر بـSettlement (UNIQUE)", True)

    try:
        conn2.execute(
            "INSERT INTO opening_party_entries (journal_entry_id, party_account_id, kind, reference, "
            "original_amount_foreign, currency_code, exchange_rate, amount_base, opening_date) "
            "VALUES (2, 1, 'receivable', 'X', -50, 'USD', 1, -50, '2026-02-01')"
        )
        check("DB (Alembic path) يرفض OpeningPartyEntry.original_amount_foreign سالب", False, "لم يُرفَض!")
    except _sqlite3.IntegrityError:
        check("DB (Alembic path) يرفض OpeningPartyEntry.original_amount_foreign سالب", True)

    try:
        conn2.execute(
            "INSERT INTO settlement_allocations (settlement_id, invoice_id, opening_party_entry_id, "
            "amount_foreign, fx_amount) VALUES (1, 1, NULL, -10, 0)"
        )
        check("DB (Alembic path) يرفض SettlementAllocation.amount_foreign سالب", False, "لم يُرفَض!")
    except _sqlite3.IntegrityError:
        check("DB (Alembic path) يرفض SettlementAllocation.amount_foreign سالب", True)

    conn2.close()

finally:
    os.unlink(db_path)

print(f"\n{'='*70}\nالنتيجة: {sum(1 for _, ok in results if ok)}/{len(results)} نجح\n{'='*70}")
print("✅ Migration/Backfill لـPhase 3B-3 نجح فعلياً — لا فقد بيانات تاريخية")
