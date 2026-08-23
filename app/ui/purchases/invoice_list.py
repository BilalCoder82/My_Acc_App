"""Purchase Invoice List — فورم رفيع فوق BaseDocumentListView"""
from __future__ import annotations
from app.models import InvoiceKind
from app.ui.common.document_list import BaseDocumentListView


class PurchaseInvoiceListView(BaseDocumentListView):
    def __init__(self, session, parent=None):
        super().__init__(
            session=session, kind=InvoiceKind.PURCHASE, title="فواتير الشراء",
            party_label="المورد", new_doc_title="فاتورة شراء جديدة", parent=parent,
        )
