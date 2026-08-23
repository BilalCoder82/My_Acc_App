"""Sales Invoice List — فورم رفيع فوق BaseDocumentListView"""
from __future__ import annotations
from app.models import InvoiceKind
from app.ui.common.document_list import BaseDocumentListView


class SalesInvoiceListView(BaseDocumentListView):
    def __init__(self, session, parent=None):
        super().__init__(
            session=session, kind=InvoiceKind.SALES, title="فواتير البيع",
            party_label="العميل", new_doc_title="فاتورة بيع جديدة", parent=parent,
        )
