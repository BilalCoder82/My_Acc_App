"""
مولّد بيانات اصطناعية لقياس G-001 — لمنتج تجاري عام قبل وجود أي عميل فعلي
=========================================================================
⚠️ تصنيف صريح بعد مراجعة: هذا "synthetic stress generator"، وليس
"realistic customer workload model". الفرق موثَّق أدناه بنداً بنداً — لا
يجوز نقل أي رقم ناتج عنه إلى G-001 على أنه "الحجم الواقعي المتوقَّع".

**ما لا يمثّله هذا المولّد (حدود صريحة، لا افتراضات ضمنية):**

A) tie-prob ليس نسبة تعادلات واقعية. `tie_prob=0.3` تعني حرفياً: "عند كل
   عملية جديدة لنفس (item, warehouse)، احتمال 30% أن تُنسَخ تاريخ آخر
   عملية سابقة لنفس المفتاح." هذا يُنتِج توزيعاً معيناً لأحجام مجموعات
   التعادل، لكنه **ليس دليلاً** على أن توزيع POS حقيقي يتبع هذا الشكل
   تحديداً — لا نعامله كافتراض "واقعي".

B) التسلسل الزمني غير طبيعي عمداً: تاريخ كل عملية يُختار عشوائياً من كامل
   المدى الزمني (`randint(0, total_days-1)`) — أي أن العملية رقم 10,000
   المُدرَجة قد تحمل تاريخاً أقدم من العملية رقم 1,000. هذا مفيد لاختبار
   backdated corrections بكثافة، لكنه **ليس نموذجاً لسجل عمليات متسلسل
   زمنياً كما يحدث فعلياً في مطعم يعمل يوماً بيوم**.

C) العدد النهائي المطبوع (`ops_attempted`) هو عدد المحاولات، لا عدد
   الحركات الناجحة فعلياً — `purchase()`/`sale()` قد تفشل (رصيد سالب مثلاً
   عبر `except Exception: continue`) وتُتجاهَل. الرقم الحقيقي هو ما يقيسه
   `measure_scale.py` لاحقاً على القاعدة الناتجة فعلياً، لا هذا الرقم.
   (يُطبَع الآن أيضاً هنا صراحة للمقارنة — انظر آخر السكربت.)

D) نموذج العمل نفسه (50% شراء/50% بيع، كمية 1-20 عشوائية موحّدة، 4 أسعار
   ثابتة) هو **stress generator عام**، لا تمثيل لأي عمل فعلي — لا مطعم
   بيتزا ولا شاورما ولا أي عميل حقيقي مستقبلي. لا يُستخدَم لاستنتاج "هذا
   الحجم المتوقَّع لعملائنا".

**الاستخدام الصحيح الوحيد لمخرجات هذا المولّد**: توليد *سيناريوهات ضغط*
لفحص كيف تتصرف أدوات القياس نفسها (ولاحقاً أي تصميم) عند أحجام مختلفة —
ليس للتنبؤ بحجم استخدام حقيقي مستقبلي، ولا لتبرير قرار معماري بمفرده.

الاستخدام:
    python tools/generate_synthetic_bench.py <out.db> --items N --warehouses W \
        --years Y --daily-rate R --tie-prob P

--tie-prob: احتمال أن تشترك حركة جديدة بنفس تاريخ حركة سابقة لنفس
            (item, warehouse) — تعريف آلي محدد أعلاه (بند A)، لا نسبة
            تعادل واقعية.
"""
from __future__ import annotations
import sys
import os
import argparse
import random
import datetime
sys.path.insert(0, ".")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base
from app.services.correction_detection import attach_detection_listeners
from tests.test_correction_scope_3b5f import seed, make_item, purchase, sale  # noqa: E402


def build(out_path: str, n_items: int, n_wh: int, years: int, daily_rate: float, tie_prob: float, seed_val: int = 42):
    if os.path.exists(out_path):
        os.remove(out_path)
    attach_detection_listeners()
    engine = create_engine(f"sqlite:///{out_path}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    coa, wh0 = seed(session)

    from app.models import Warehouse
    warehouses = [wh0]
    for i in range(1, n_wh):
        w = Warehouse(name_ar=f"WH{i}")
        session.add(w)
        session.commit()
        warehouses.append(w)

    rnd = random.Random(seed_val)
    # توزيع شعبية الأصناف منحرف (Zipf تقريبي) — أصناف قليلة عالية الحركة،
    # كثير منها منخفض — أقرب لواقع مبيعات فعلي من توزيع منتظم.
    # (هذا وحده لا يكفي لتصنيف المولّد "واقعياً" — انظر التحذيرات A-D أعلاه.)
    items = [make_item(session, coa, f"SKU{i}") for i in range(n_items)]
    weights = [1.0 / (i + 1) for i in range(n_items)]

    total_days = years * 365
    start = datetime.date(2022, 1, 1)
    day_cursor = start
    n_ops = int(total_days * daily_rate)
    last_date_per_key = {}
    n_success = 0
    n_failed = 0

    for op in range(n_ops):
        item = rnd.choices(items, weights=weights, k=1)[0]
        wh = rnd.choice(warehouses)
        key = (item.id, wh.id)
        if key in last_date_per_key and rnd.random() < tie_prob:
            d = last_date_per_key[key]
        else:
            offset = rnd.randint(0, total_days - 1)
            d = start + datetime.timedelta(days=offset)
        last_date_per_key[key] = d
        qty = rnd.randint(1, 20)
        price = rnd.choice([10, 15, 20, 25])
        ref = f"OP{op}"
        try:
            if rnd.random() < 0.5:
                purchase(session, item, wh, qty, price, d, ref)
            else:
                sale(session, item, wh, qty, price, d, ref)
            n_success += 1
        except Exception:
            session.rollback()
            n_failed += 1
            continue
        if op % 500 == 0:
            print(f"  ... {op}/{n_ops}", file=sys.stderr)

    session.close()
    print(f"built {out_path}: items={n_items} warehouses={n_wh} years={years} "
          f"daily_rate={daily_rate} tie_prob={tie_prob}")
    print(f"  ops_attempted={n_ops}  succeeded={n_success}  failed_skipped={n_failed}  "
          f"(النسبة الناجحة فعلياً={n_success/n_ops*100:.1f}% — هذا الرقم، لا "
          f"ops_attempted، هو ما يعكس عدد الحركات الفعلي الذي سيقيسه measure_scale.py)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--items", type=int, default=20)
    ap.add_argument("--warehouses", type=int, default=2)
    ap.add_argument("--years", type=int, default=2)
    ap.add_argument("--daily-rate", type=float, default=5.0)
    ap.add_argument("--tie-prob", type=float, default=0.3)
    args = ap.parse_args()
    build(args.out, args.items, args.warehouses, args.years, args.daily_rate, args.tie_prob)
