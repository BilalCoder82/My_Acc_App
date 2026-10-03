"""
قياس Candidate-space الفعلي لكل Scope حقيقي (لا لكل history كامل)
====================================================================
يصحح خطأ القياس السابق: بدل ضرب Q2.5-count عبر كل مجموعات تعادل تاريخ
الصنف بأكمله، يبني الـScope الفعلي (`build_impact_scope`) لعدة جذور
حقيقية موجودة بالفعل في القاعدة (أنشأتها detection hooks تلقائياً أثناء
التوليد — لا إنشاء يدوي)، ثم يحسب Q2.5-count **داخل ذلك الـScope فقط**.

عيّنة الجذور: قديم/وسط/حديث (بالتاريخ) + عيّنة عشوائية إضافية — بحيث لا
تُبنى النتيجة على ثلاث نقاط مختارة يدوياً فقط.

لكل جذر يُطبَع: حجم الـScope، عدد مجموعات التعادل داخله، أكبر مجموعة،
Q2.5 candidate count، log10 له، وCandidate×Scope. لا هوية صنف ولا بيانات
مالية — فقط أرقام وIDs داخلية.

⚠️ هذا قياس فقط، لا حكم: لا نستنتج من نتيجته وحده "نحتاج/لا نحتاج
Execution Model خاصاً" — هذا قرار لاحق يجمع هذا مع عدد correction events
المحتملة الإجمالي، كما اتُّفِق.

الاستخدام:
    python tools/measure_actual_scope_candidates.py /path/to/<company>.db [--samples N]
"""
from __future__ import annotations
import sys
import math
import random
import argparse
from collections import defaultdict
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, ".")
from app.models import (  # noqa: E402
    CorrectionEvent, CorrectionEventRootCause, CorrectionEventImpactScopeElement,
    InventoryMovement, MovementDirection,
)
from app.services.correction_scope import build_impact_scope  # noqa: E402


def log10_bigint(n: int) -> float:
    if n <= 0:
        return 0.0
    return n.bit_length() * math.log10(2)


def q25_count(p_in: int, p_out: int) -> int:
    return math.comb(p_in + p_out, p_in)


def pick_roots(session, n_random: int, seed_val: int = 7):
    # كل جذر مصدره InventoryMovement (component_type=NEW_MOVEMENT) — الأنماط
    # الأخرى (REVERSAL/REPLACEMENT) خارج هذا القياس الأولي عمداً.
    rows = session.execute(
        select(CorrectionEvent.id, CorrectionEventRootCause.source_id)
        .join(CorrectionEventRootCause, CorrectionEventRootCause.correction_event_id == CorrectionEvent.id)
        .where(CorrectionEventRootCause.source_type == "inventory_movement")
    ).all()
    if not rows:
        return []
    event_ids = [r[0] for r in rows]
    mvt_ids = [r[1] for r in rows]
    dates = {
        m.id: m.movement_date
        for m in session.execute(
            select(InventoryMovement).where(InventoryMovement.id.in_(mvt_ids))
        ).scalars().all()
    }
    annotated = sorted(
        ((event_id, dates[mvt_id]) for event_id, mvt_id in zip(event_ids, mvt_ids) if mvt_id in dates),
        key=lambda x: x[1],
    )
    if not annotated:
        return []
    picks = {}
    picks["oldest"] = annotated[0][0]
    picks["median"] = annotated[len(annotated) // 2][0]
    picks["newest"] = annotated[-1][0]
    rnd = random.Random(seed_val)
    pool = [e for e, _ in annotated if e not in picks.values()]
    for i, e in enumerate(rnd.sample(pool, min(n_random, len(pool)))):
        picks[f"random_{i}"] = e
    return list(picks.items())


def measure_one(session, label: str, event_id: int):
    build_impact_scope(session, event_id)
    session.commit()

    elements = session.execute(
        select(CorrectionEventImpactScopeElement.source_id).where(
            CorrectionEventImpactScopeElement.correction_event_id == event_id,
            CorrectionEventImpactScopeElement.source_type == "inventory_movement",
        )
    ).scalars().all()
    scope_size = len(elements)
    if scope_size == 0:
        print(f"[{label}] event={event_id}: scope فارغ — تجاوز")
        return None

    movements = session.execute(
        select(InventoryMovement.item_id, InventoryMovement.warehouse_id,
               InventoryMovement.movement_date, InventoryMovement.direction)
        .where(InventoryMovement.id.in_(elements))
    ).all()

    date_map = defaultdict(list)  # (item,wh,date) -> [direction,...]
    for item_id, wh_id, d, direction in movements:
        date_map[(item_id, wh_id, d)].append(direction)

    tie_groups = [v for v in date_map.values() if len(v) > 1]
    n_ties = len(tie_groups)
    max_tie = max((len(v) for v in tie_groups), default=0)

    q25_total = 1
    for directions in tie_groups:
        n = len(directions)
        p_in = sum(1 for x in directions if x == MovementDirection.IN)
        p_out = n - p_in
        q25_total *= q25_count(p_in, p_out)

    cxs = q25_total * scope_size
    print(f"[{label}] event={event_id}: scope_size={scope_size}  tie_groups_in_scope={n_ties}  "
          f"max_tie_group={max_tie}  Q2.5_candidates={q25_total if q25_total < 10**9 else f'≈10^{log10_bigint(q25_total):.1f}'}  "
          f"log10(candidates)={log10_bigint(q25_total):.2f}  "
          f"candidate×scope={cxs if cxs < 10**9 else f'≈10^{log10_bigint(cxs):.1f}'}")
    return dict(label=label, scope_size=scope_size, n_ties=n_ties, max_tie=max_tie,
                q25_total=q25_total, log10_q25=log10_bigint(q25_total), cxs=cxs)


def main(db_path: str, n_random: int):
    engine = create_engine(f"sqlite:///{db_path}")
    session = Session(bind=engine)

    roots = pick_roots(session, n_random)
    if not roots:
        print("لا توجد CorrectionEvent بجذر inventory_movement في هذه القاعدة — لا شيء لقياسه.")
        return

    print(f"عدد الجذور المُختارة: {len(roots)} (oldest/median/newest + {n_random} عشوائية)\n")
    results = [measure_one(session, label, eid) for label, eid in roots]
    results = [r for r in results if r]

    if results:
        print("\n=== ملخص ===")
        print(f"أكبر Q2.5_candidates عبر العيّنة: log10≈{max(r['log10_q25'] for r in results):.2f}")
        print(f"أصغر Q2.5_candidates عبر العيّنة: log10≈{min(r['log10_q25'] for r in results):.2f}")
        print("(لاحظ: القياس ذاته أنشأ صفوف ImpactScopeElement/TieGroup فعلية في هذه "
              "القاعدة — ملف اختبار/قياس، لا يُستخدَم لاحقاً كقاعدة إنتاج حقيقية.)")

    session.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("db_path")
    ap.add_argument("--samples", type=int, default=5, help="عدد الجذور العشوائية الإضافية")
    args = ap.parse_args()
    main(args.db_path, args.samples)
