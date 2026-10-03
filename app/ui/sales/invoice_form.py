"""Sales Invoice Form — فورم رفيع فوق BaseDocumentFormView (فاتورة البيع = المرجع الأساسي)"""
from __future__ import annotations
from app.models import InvoiceKind
from app.services.posting import post_sales_invoice
from app.ui.common.document_form import BaseDocumentFormView


class SalesInvoiceFormView(BaseDocumentFormView):
    def __init__(self, session, invoice_id: int | None = None, parent=None):
        super().__init__(
            session=session, doc_title="فاتورة بيع", kind=InvoiceKind.SALES,
            party_label="العميل", is_customer=True, posting_fn=post_sales_invoice,
            ref_prefix="JE-SAL", invoice_id=invoice_id, is_return=False, parent=parent,
        )
