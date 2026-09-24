"""
G-001 Discovery — قياس الحجم الفعلي (read-only بالكامل، لا كتابة إطلاقاً)
=========================================================================
الاستخدام:
    python measure_scale.py /path/to/<client>.db

يطبع فقط أرقاماً إحصائية (Max + Percentiles، لا Average فقط كما اتُّفِق) —
لا يقرأ أي محتوى محاسبي أو أسماء عملاء/أصناف، فقط عدّاداً رقمياً:

1) Movement dimension: إجمالي InventoryMovement، وتوزيع عدد الحركات لكل
   (item_id, warehouse_id) — Max + p50/p90/p99.
2) Same-date tie dimension: لكل (item_id, warehouse_id, movement_date) بها
   أكثر من حركة واحدة → حجم المجموعة. يقيس Max + توزيع عدد هذه المجموعات
   لكل (item,warehouse)، وهو المؤشر الأقرب لعدد الـtie-groups المحتملة.
3) Candidate-space (تقديري وليس دقيقاً): لكل history، حاصل ضرب تقريبي
   لأحجام مجموعات التعادل فيه (Π group_size!) كحد أعلى نظري خام — يُطبَع
   بوضوح كـ"upper bound خام، ليس عدد Candidates الفعلي بعد قيود Q2.5"،
   لتفادي الإيحاء بدقة غير موجودة.

آمن للتشغيل على قاعدة عميل فعلية: لا UPDATE، لا INSERT، لا حتى session
كتابة — اتصال SQLite بوضع القراءة فقط صراحة (immutable/read-only URI) حيث
مدعوم، وإلا SELECT فقط عبر SQLAlchemy بلا أي commit.
"""
from __future__ import annotations
import sys
import statistics
from collections import defaultdict
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, ".")
from app.models import InventoryMovement  # noqa: E402


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0
    k = (len(sorted_vals) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def fmt_stats(label, values):
    if not values:
        print(f"{label}: لا بيانات")
        return
    s = sorted(values)
    print(f"{label}:")
    print(f"  count={len(values)}  max={s[-1]}  p99={percentile(s,0.99):.1f}  "
          f"p90={percentile(s,0.90):.1f}  p50={percentile(s,0.50):.1f}  min={s[0]}")


def factorial(n):
    r = 1
    for i in range(2, n + 1):
        r *= i
    return r


def main(db_path: str):
    # اتصال قراءة فقط صراحة — يفشل بوضوح لو حاول أي كود لاحقاً الكتابة
    engine = create_engine(f"sqlite:///file:{db_path}?mode=ro&uri=true")
    session = Session(bind=engine)

    # 1) Movement dimension
    total = session.execute(select(InventoryMovement.id)).scalars().all()
    print(f"=== 1) Movement dimension ===")
    print(f"إجمالي InventoryMovement: {len(total)}\n")

    rows = session.execute(
        select(InventoryMovement.item_id, InventoryMovement.warehouse_id,
               InventoryMovement.movement_date, InventoryMovement.id)
    ).all()

    per_history = defaultdict(int)
    same_date_groups = defaultdict(lambda: defaultdict(int))  # (item,wh) -> date -> count
    for item_id, wh_id, mdate, _id in rows:
        key = (item_id, wh_id)
        per_history[key] += 1
        same_date_groups[key][mdate] += 1

    fmt_stats("توزيع عدد الحركات لكل (item, warehouse)", list(per_history.values()))

    print(f"\n=== 2) Same-date tie dimension ===")
    tie_group_sizes = []       # كل مجموعة تعادل (>1) بحجمها
    ties_per_history = []      # لكل (item,wh): كم مجموعة تعادل بها؟
    max_group_examples = []
    for key, date_counts in same_date_groups.items():
        n_ties = 0
        for d, cnt in date_counts.items():
            if cnt > 1:
                tie_group_sizes.append(cnt)
                n_ties += 1
        ties_per_history.append(n_ties)

    fmt_stats("حجم مجموعات التعادل (عدد حركات بنفس movement_date)", tie_group_sizes)
    fmt_stats("عدد مجموعات التعادل لكل (item, warehouse)", ties_per_history)

    print(f"\n=== 3) Candidate-space — تقدير خام فقط (upper bound نظري، NOT دقيق) ===")
    print("تحذير: هذا حاصل ضرب group_size! لكل مجموعات التعادل في نفس الـhistory")
    print("— لا يعكس قيود Q2.5 (consecutive-run freedom) التي تُخفِّض الرقم الحقيقي")
    print("فعلياً في أغلب الحالات. استخدمه فقط كحد أعلى تقريبي لأولوية القياس.\n")

    per_history_ceiling = []
    for key, date_counts in same_date_groups.items():
        ceiling = 1
        for d, cnt in date_counts.items():
            if cnt > 1:
                ceiling *= factorial(cnt)
        if ceiling > 1:
            per_history_ceiling.append(ceiling)

    fmt_stats("سقف تقديري خام لعدد الترتيبات لكل history المتأثر بتعادل", per_history_ceiling)
    if per_history_ceiling:
        top5 = sorted(per_history_ceiling, reverse=True)[:5]
        print(f"\n  أعلى 5 قيم (بلا تحديد هوية الصنف): {top5}")

    session.close()
    print("\n(انتهى — لم تُفتَح أي كتابة على القاعدة)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("الاستخدام: python measure_scale.py /path/to/<client>.db")
        sys.exit(1)
    main(sys.argv[1])
