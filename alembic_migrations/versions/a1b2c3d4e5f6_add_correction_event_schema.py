"""PHASE3B5C — Correction Event schema (schema only, no Historical Correction logic)

Revision ID: a1b2c3d4e5f6
Revises: e5f9a3c7d2b1
Create Date: 2026-09-13 00:00:00.000000

راجع 3B-5-C_DISCOVERY_DESIGN_QUESTIONS.md (Q1–Q3.6 + Pre-Implementation
Schema Audit) قبل تعديل أي شيء هنا — هذه الهجرة تنفّذ فقط ما وثّقته تلك
المرحلة حرفياً: عشرة جداول جديدة، بلا أي منطق Detection/Analysis/
Recalculation، وبلا تعديل على أي جدول موجود مسبقاً.

قرار متعمَّد واحد يستحق التوثيق هنا تحديداً: correction_events.approved_
candidate_state_id لا يحمل قيد FK حقيقياً على مستوى القاعدة، رغم أنه
يشير دلالياً إلى correction_event_candidate_states.id. السبب: علاقة
دائرية حقيقية بين جدولين جديدين معاً (كل منهما يشير للآخر) — بدل معالجتها
بإنشاء الجداول على مرحلتين ثم ALTER (وbatch mode على SQLite يعيد بناء
الجدول كاملاً لكل ALTER، مما يعقّد الترتيب الدائري بلا داعٍ حقيقي)، عولجت
بنفس أسلوب source_type/source_id المرجعي القائم فعلاً في هذا المشروع
لحقول أخرى مشابهة: عمود عادي، والإنفاذ الفعلي يبقى مسؤولية طبقة الخدمة
لاحقاً — تماماً كما لا يوجد اليوم قيد DB على source_type/source_id في
InventoryMovement أو JournalEntry.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e5f9a3c7d2b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'correction_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('item_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('trigger_type', sa.String(length=30), nullable=True),
        sa.Column(
            'status', sa.Enum(
                'candidate', 'analysis', 'pending', 'approved',
                'accounting_correction', 'stale', 'withdrawn',
                name='correctioneventstatus',
            ),
            nullable=False, server_default='candidate',
        ),
        sa.Column('chronology_basis', sa.Enum('known', 'assumed', name='chronologybasis'), nullable=True),
        sa.Column('scope_completeness', sa.Enum('complete', 'partial', name='scopecompleteness'), nullable=True),
        sa.Column('analysis_as_of', sa.DateTime(), nullable=True),
        # لا FK حقيقي عمداً — راجع الشرح أعلى الملف.
        sa.Column('approved_candidate_state_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_correction_events_item_id', 'correction_events', ['item_id'])
    op.create_index('ix_correction_events_status', 'correction_events', ['status'])

    op.create_table(
        'correction_event_root_causes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('correction_event_id', sa.Integer(), sa.ForeignKey('correction_events.id'), nullable=False),
        sa.Column(
            'component_type',
            sa.Enum('new_movement', 'reversal', 'replacement', name='rootcausecomponenttype'),
            nullable=False,
        ),
        sa.Column('source_type', sa.String(length=30), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=True),
    )
    op.create_index('ix_correction_event_root_causes_event', 'correction_event_root_causes', ['correction_event_id'])
    op.create_index('ix_correction_event_root_causes_source', 'correction_event_root_causes', ['source_type', 'source_id'])

    op.create_table(
        'correction_event_tie_groups',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('correction_event_id', sa.Integer(), sa.ForeignKey('correction_events.id'), nullable=False),
        sa.Column('tied_movement_date', sa.Date(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
    )
    op.create_index('ix_correction_event_tie_groups_event', 'correction_event_tie_groups', ['correction_event_id'])

    op.create_table(
        'correction_event_ordering_assumptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tie_group_id', sa.Integer(), sa.ForeignKey('correction_event_tie_groups.id'), nullable=False),
        sa.Column('ordering_representation', sa.Text(), nullable=False),
    )
    op.create_index('ix_correction_event_ordering_assumptions_tiegroup', 'correction_event_ordering_assumptions', ['tie_group_id'])

    op.create_table(
        'correction_event_candidate_states',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('correction_event_id', sa.Integer(), sa.ForeignKey('correction_events.id'), nullable=False),
        sa.Column('label', sa.String(length=10), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_correction_event_candidate_states_event', 'correction_event_candidate_states', ['correction_event_id'])

    op.create_table(
        'correction_event_candidate_ordering_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('candidate_state_id', sa.Integer(), sa.ForeignKey('correction_event_candidate_states.id'), nullable=False),
        sa.Column('ordering_assumption_id', sa.Integer(), sa.ForeignKey('correction_event_ordering_assumptions.id'), nullable=False),
        sa.Column('tie_group_id', sa.Integer(), sa.ForeignKey('correction_event_tie_groups.id'), nullable=False),
        sa.UniqueConstraint(
            'candidate_state_id', 'tie_group_id',
            name='uq_candidate_one_assumption_per_tiegroup',
        ),
    )
    op.create_index('ix_correction_event_candidate_ordering_links_candidate', 'correction_event_candidate_ordering_links', ['candidate_state_id'])

    op.create_table(
        'correction_event_delta_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('candidate_state_id', sa.Integer(), sa.ForeignKey('correction_event_candidate_states.id'), nullable=False),
        sa.Column('movement_id', sa.Integer(), sa.ForeignKey('inventory_movements.id'), nullable=False),
        sa.Column('delta_quantity', sa.Numeric(14, 3), nullable=True),
        sa.Column('delta_inventory_value', sa.Numeric(14, 4), nullable=False),
    )
    op.create_index('ix_correction_event_delta_records_candidate', 'correction_event_delta_records', ['candidate_state_id'])

    op.create_table(
        'correction_event_impact_scope_elements',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('correction_event_id', sa.Integer(), sa.ForeignKey('correction_events.id'), nullable=False),
        sa.Column('kind', sa.Enum('movement', 'document', 'accounting_effect', name='impactscopeelementkind'), nullable=False),
        sa.Column('source_type', sa.String(length=30), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            'correction_event_id', 'source_type', 'source_id',
            name='uq_scope_element_no_duplicate',
        ),
    )
    op.create_index('ix_correction_event_impact_scope_elements_event', 'correction_event_impact_scope_elements', ['correction_event_id'])
    op.create_index('ix_correction_event_impact_scope_elements_source', 'correction_event_impact_scope_elements', ['source_type', 'source_id'])

    op.create_table(
        'correction_event_impact_scope_relationships',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('correction_event_id', sa.Integer(), sa.ForeignKey('correction_events.id'), nullable=False),
        sa.Column('from_element_id', sa.Integer(), sa.ForeignKey('correction_event_impact_scope_elements.id'), nullable=False),
        sa.Column('to_element_id', sa.Integer(), sa.ForeignKey('correction_event_impact_scope_elements.id'), nullable=False),
        sa.Column(
            'relationship_type',
            sa.Enum('causes', 'precedes', 'propagates_to', 'belongs_to', 'produces_accounting_effect', name='impactscoperelationshiptype'),
            nullable=False,
        ),
    )
    op.create_index('ix_correction_event_impact_scope_relationships_event', 'correction_event_impact_scope_relationships', ['correction_event_id'])

    op.create_table(
        'correction_event_accounting_corrections',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('correction_event_id', sa.Integer(), sa.ForeignKey('correction_events.id'), nullable=False),
        sa.Column('journal_entry_id', sa.Integer(), sa.ForeignKey('journal_entries.id'), nullable=False, unique=True),
    )
    op.create_index('ix_correction_event_accounting_corrections_event', 'correction_event_accounting_corrections', ['correction_event_id'])


def downgrade() -> None:
    op.drop_table('correction_event_accounting_corrections')
    op.drop_table('correction_event_impact_scope_relationships')
    op.drop_table('correction_event_impact_scope_elements')
    op.drop_table('correction_event_delta_records')
    op.drop_table('correction_event_candidate_ordering_links')
    op.drop_table('correction_event_candidate_states')
    op.drop_table('correction_event_ordering_assumptions')
    op.drop_table('correction_event_tie_groups')
    op.drop_table('correction_event_root_causes')
    op.drop_table('correction_events')
