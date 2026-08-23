"""Sales Return Form — فورم رفيع فوق BaseDocumentFormView"""
from __future__ import annotations
from app.models import InvoiceKind
from app.services.returns import post_sales_return
from app.ui.common.document_form import BaseDocumentFormView


class SalesReturnInvoiceFormView(BaseDocumentFormView):
    def __init__(self, session, invoice_id: int | None = None, parent=None):
        super().__init__(
            session=session, doc_title="مرتجع بيع", kind=InvoiceKind.SALES_RETURN,
            party_label="العميل", is_customer=True, posting_fn=post_sales_return,
            ref_prefix="JE-SRET", invoice_id=invoice_id, is_return=True, parent=parent,
        )
