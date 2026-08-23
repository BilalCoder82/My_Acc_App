"""Purchase Return List — فورم رفيع فوق BaseDocumentListView"""
from __future__ import annotations
from app.models import InvoiceKind
from app.ui.common.document_list import BaseDocumentListView


class PurchaseReturnInvoiceListView(BaseDocumentListView):
    def __init__(self, session, parent=None):
        super().__init__(
            session=session, kind=InvoiceKind.PURCHASE_RETURN, title="مرتجعات الشراء",
            party_label="المورد", new_doc_title="مرتجع شراء جديد",
            show_original_ref=True, parent=parent,
        )
