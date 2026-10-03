"""Purchase Invoice Form — فورم رفيع فوق BaseDocumentFormView"""
from __future__ import annotations
from app.models import InvoiceKind
from app.services.posting import post_purchase_invoice
from app.ui.common.document_form import BaseDocumentFormView


class PurchaseInvoiceFormView(BaseDocumentFormView):
    def __init__(self, session, invoice_id: int | None = None, parent=None):
        super().__init__(
            session=session, doc_title="فاتورة شراء", kind=InvoiceKind.PURCHASE,
            party_label="المورد", is_customer=False, posting_fn=post_purchase_invoice,
            ref_prefix="JE-PUR", invoice_id=invoice_id, is_return=False, parent=parent,
        )
