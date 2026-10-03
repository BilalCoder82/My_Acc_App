"""
عدّاد Candidate-space الحقيقي وفق Q2.5 (لا factorial خام) + Candidate×Scope
============================================================================
هذا أداة Discovery/قياس بحتة — قراءة فقط، لا يلمس أي كود إنتاج، لا يغيّر
أي جدول، ولا يمثّل تصميم G-001. الهدف الوحيد: استبدال المقياس الخاطئ
(Π group_size!) بالعدد الحقيقي المثبَت في 3B-5-C_DISCOVERY_DESIGN_
QUESTIONS.md § Q2.5.

القاعدة الرياضية المطبَّقة هنا (مأخوذة حرفياً من نتائج Q2.5 المُثبَتة):
- IN-IN يتبادل تماماً (يتبادل = ترتيبهما الداخلي لا يغيّر النتيجة الاقتصادية).
- OUT-OUT يتبادل تماماً.
- IN-OUT لا يتبادل عموماً (مُثبَت بمثال رقمي: 520 مقابل 560).
- الاختزال الآمن الوحيد المُثبَت: تقسيم مجموعة التعادل إلى "runs" قصوى
  متتالية من نفس النوع؛ الترتيب الداخلي لكل run لا يغيّر النتيجة، لكن
  *تداخل* الـruns مع بعضها (أيّ تسلسل IN/OUT الكلي) **لم يُختزَل** — هذا
  ما زال يحتاج تعداداً كاملاً.
- إذن: العدد الحقيقي المُثبَت لمجموعة تعادل من p حركة IN وq حركة OUT هو
  **C(p+q, p)** (عدد تسلسلات IN/OUT المميزة) — لا (p+q)! ولا 1 (لأن
  التداخل بين الأنواع غير مُختزَل، فقط الترتيب الداخلي لكل نوع منفرد).
  هذا لا يزال "حد أعلى مُثبَت"، وليس بالضرورة عدد النتائج الاقتصادية
  المميزة الفعلي (Q2.5 نفسها تقول صراحة: هل يوجد اختزال أضيق من
  run-collapsed — "not investigated"). لذلك يُعرَض دوماً بجانب رقم
  factorial الخام للمقارنة، لا كبديل نهائي "صحيح 100%".

⚠️ Transfer: تُعامَل الحركة كـIN أو OUT حسب حقل `direction` المخزَّن فعلياً
لكل طرف (الساق OUT في المستودع المصدر، الساق IN في المستودع الهدف) —
مطابق تماماً لما اختبرته Q2.5 نفسها ("Transfer treated as OUT+IN").

الاستخدام:
    python tools/q25_candidate_count.py /path/to/<company>.db
"""
from __future__ import annotations
import sys
import math
from collections import defaultdict
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, ".")
from app.models import InventoryMovement, MovementDirection  # noqa: E402


def log10_bigint(n: int) -> float:
    if n <= 0:
        return 0.0
    return n.bit_length() * math.log10(2)


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0
    k = (len(sorted_vals) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def fmt_magnitude_stats(label, values, raw_ok_max=10**6):
    if not values:
        print(f"{label}: لا بيانات")
        return
    mags = sorted(log10_bigint(v) for v in values)
    small_enough = all(v <= raw_ok_max for v in values)
    if small_enough:
        s = sorted(values)
        print(f"{label}: count={len(s)}  max={s[-1]}  p90={percentile(s,0.90):.1f}  "
              f"p50={percentile(s,0.50):.1f}  min={s[0]}")
    else:
        print(f"{label} (رتبة حجم log10 — الأرقام تجاوزت نطاقاً قابلاً للعرض المباشر):")
        print(f"  count={len(mags)}  max≈10^{mags[-1]:.1f}  p90≈10^{percentile(mags,0.90):.1f}  "
              f"p50≈10^{percentile(mags,0.50):.1f}  min≈10^{mags[0]:.1f}")


def factorial(n):
    r = 1
    for i in range(2, n + 1):
        r *= i
    return r


def q25_count(p_in: int, p_out: int) -> int:
    """العدد المُثبَت (run-collapsed) لمجموعة تعادل من p_in حركة IN وp_out
    حركة OUT = C(p_in+p_out, p_in)."""
    return math.comb(p_in + p_out, p_in)


def main(db_path: str):
    engine = create_engine(f"sqlite:///file:{db_path}?mode=ro&uri=true")
    session = Session(bind=engine)

    rows = session.execute(
        select(InventoryMovement.item_id, InventoryMovement.warehouse_id,
               InventoryMovement.movement_date, InventoryMovement.direction)
    ).all()
    session.close()

    same_date_groups = defaultdict(lambda: defaultdict(list))  # (item,wh) -> date -> [direction,...]
    per_history_count = defaultdict(int)
    for item_id, wh_id, mdate, direction in rows:
        key = (item_id, wh_id)
        per_history_count[key] += 1
        same_date_groups[key][mdate].append(direction)

    raw_ceiling_per_history = []
    q25_count_per_history = []
    tie_group_raw = []
    tie_group_q25 = []
    candidate_x_scope = []  # q25 count للـhistory × عدد حركاته (proxy لتكلفة إعادة الحساب)

    for key, date_map in same_date_groups.items():
        raw_ceiling = 1
        q25_total = 1
        has_tie = False
        for d, directions in date_map.items():
            if len(directions) <= 1:
                continue
            has_tie = True
            n = len(directions)
            p_in = sum(1 for x in directions if x == MovementDirection.IN)
            p_out = n - p_in
            raw_g = factorial(n)
            q25_g = q25_count(p_in, p_out)
            tie_group_raw.append(raw_g)
            tie_group_q25.append(q25_g)
            raw_ceiling *= raw_g
            q25_total *= q25_g
        if has_tie:
            raw_ceiling_per_history.append(raw_ceiling)
            q25_count_per_history.append(q25_total)
            candidate_x_scope.append(q25_total * per_history_count[key])

    print("=== مقارنة: raw factorial ceiling مقابل Q2.5-constrained count (لكل مجموعة تعادل منفردة) ===")
    fmt_magnitude_stats("Raw factorial (group_size!)", tie_group_raw)
    fmt_magnitude_stats("Q2.5-constrained (C(p_in+p_out, p_in))", tie_group_q25)

    print("\n=== نفس المقارنة، مجمَّعة لكل (item, warehouse) بأكملها (حاصل ضرب كل مجموعاته) ===")
    fmt_magnitude_stats("Raw factorial ceiling — لكل history", raw_ceiling_per_history)
    fmt_magnitude_stats("Q2.5-constrained count — لكل history", q25_count_per_history)

    print("\n=== البُعد الثالث: Candidate × Scope (تقريب خام لتكلفة إعادة الحساب) ===")
    print("= (عدد Candidates وفق Q2.5) × (عدد حركات الـhistory نفسه)")
    fmt_magnitude_stats("Candidate × Scope", candidate_x_scope)

    print("\n⚠️ تذكير: هذا لا يزال حداً أعلى مُثبَتاً، لا عدد النتائج الاقتصادية")
    print("المميزة الفعلي بالضرورة (سؤال مفتوح في Q2.5 نفسها، غير محسوم).")
    print("(انتهى — قراءة فقط، لا كتابة)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("الاستخدام: python tools/q25_candidate_count.py /path/to/<company>.db")
        sys.exit(1)
    main(sys.argv[1])
