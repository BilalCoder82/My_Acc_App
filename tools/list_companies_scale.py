"""
تشخيص سريع — أي ملف عميل هو الصحيح؟ (قراءة فقط بالكامل)
========================================================
الاستخدام:
    python tools/list_companies_scale.py /path/to/data/companies

يفحص كل ملفات .db داخل هذا المجلد (كل عميل/شركة) ويطبع سطراً واحداً لكل
ملف: اسم الملف + عدد InventoryMovement + عدد Invoice + عدد Item.
لا يفتح أي كتابة، ولا يطبع أي اسم عميل أو محتوى مالي — فقط اسم الملف
وأعداد. الهدف: تحديد الملف الصحيح الذي يحمل بيانات فعلية قبل تشغيل
measure_scale.py الكامل عليه.

إن لم تكن متأكداً من المسار: registry.db (بجانب مجلد companies/) يحتوي
عمود db_filename لكل شركة مسجَّلة — يمكن فتحه بنفس الطريقة أيضاً لو أردت
مطابقة الاسم باسم الشركة الفعلي (هذا وحده سيُظهر أسماء شركات، فتجنّب
مشاركة مخرجاته إن كنت تفضّل عدم كشف ذلك).
"""
from __future__ import annotations
import sys
import os
import glob
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

sys.path.insert(0, ".")
from app.models import InventoryMovement, Invoice, Item  # noqa: E402


def scan_one(path: str):
    try:
        engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
        s = Session(bind=engine)
        mv = s.execute(select(func.count(InventoryMovement.id))).scalar_one()
        inv = s.execute(select(func.count(Invoice.id))).scalar_one()
        it = s.execute(select(func.count(Item.id))).scalar_one()
        s.close()
        return mv, inv, it, None
    except Exception as e:
        return None, None, None, str(e).splitlines()[0][:120]


def main(companies_dir: str):
    files = sorted(glob.glob(os.path.join(companies_dir, "*.db")))
    if not files:
        print(f"لا ملفات .db داخل: {companies_dir}")
        return
    print(f"{'file':40s} {'movements':>10s} {'invoices':>10s} {'items':>8s}")
    for f in files:
        mv, inv, it, err = scan_one(f)
        name = os.path.basename(f)
        if err:
            print(f"{name:40s}  تعذّر الفتح: {err}")
        else:
            print(f"{name:40s} {mv:10d} {inv:10d} {it:8d}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("الاستخدام: python tools/list_companies_scale.py /path/to/data/companies")
        sys.exit(1)
    main(sys.argv[1])
