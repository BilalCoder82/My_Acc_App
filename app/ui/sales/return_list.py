"""Sales Return List — فورم رفيع فوق BaseDocumentListView"""
from __future__ import annotations
from app.models import InvoiceKind
from app.ui.common.document_list import BaseDocumentListView


class SalesReturnInvoiceListView(BaseDocumentListView):
    def __init__(self, session, parent=None):
        super().__init__(
            session=session, kind=InvoiceKind.SALES_RETURN, title="مرتجعات البيع",
            party_label="العميل", new_doc_title="مرتجع بيع جديد",
            show_original_ref=True, parent=parent,
        )
