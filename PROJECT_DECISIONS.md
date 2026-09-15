# PROJECT_DECISIONS.md
سجل قرارات معمارية محددة ومتعمَّدة (deliberate deviations من الأنماط المعتادة في المشروع) — لا يُقرأ كسياسة عامة قابلة للنسخ التلقائي؛ كل قرار هنا خاص بسياقه، ويجب تقييمه من جديد إن ظهرت حالة مشابهة، لا تطبيقه تلقائياً.

---

## PD-001 — `CorrectionEvent.approved_candidate_state_id` بلا Foreign Key حقيقي

**التاريخ**: PHASE 3B-5-C (Correction Event schema implementation).

**القرار**:
> `approved_candidate_state_id` does not use a database FK because of the circular dependency between `CorrectionEvent` and `CandidateState`; referential validity is enforced at application/workflow level.

**السياق الكامل**: `correction_events.approved_candidate_state_id` يشير دلالياً إلى `correction_event_candidate_states.id`، لكن `correction_event_candidate_states.correction_event_id` يشير بدوره إلى `correction_events.id` — علاقة دائرية حقيقية بين جدولين جديدين معاً أُنشئا في نفس الهجرة. البديل (إنشاء الجدولين على مرحلتين ثم `ALTER` لإضافة القيد) كان سيضيف تعقيداً حقيقياً بلا فائدة تناسبه على SQLite تحديداً، حيث يعيد `batch_alter_table` بناء الجدول بالكامل لكل `ALTER`.

**البديل المُتَّبَع**: عمود عادي بلا قيد FK على مستوى القاعدة — بنفس روح نمط `source_type`/`source_id` الموجود أصلاً في `InventoryMovement`/`JournalEntry` (والذي لا يحمل هو الآخر قيد FK حقيقياً). صحة الإشارة تبقى مسؤولية طبقة الخدمة (Python)، لا قيداً تفرضه قاعدة البيانات.

**مرجع التنفيذ الكامل**: `alembic_migrations/versions/a1b2c3d4e5f6_add_correction_event_schema.py` (التعليق التوضيحي أعلى الملف)، و`app/models.py` (تعليق `CorrectionEvent.approved_candidate_state_id`)، و`3B-5-C_DISCOVERY_DESIGN_QUESTIONS.md` (قسم Pre-Implementation Schema Audit).

**تنبيه صريح — لا تعميم تلقائي**: هذا القرار خاص بهذه الحالة تحديداً (علاقة دائرية بين جدولين جديدين، مع بديل مكلف نسبياً على SQLite). ظهور علاقة دائرية أخرى مستقبلاً **لا يعني تلقائياً** أن نفس الحل (إسقاط الـFK) هو الصحيح — يجب تقييم كل حالة من جديد وفق كلفتها وسياقها الخاص، لا استنساخ هذا القرار لمجرد التشابه السطحي.

**الحالة**: نشِط، موثَّق، مُراقَب — لم يُطرَح بعد أي دليل يستدعي إعادة فتحه.
