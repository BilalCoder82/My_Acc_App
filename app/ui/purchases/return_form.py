"""Purchase Return Form — فورم رفيع فوق BaseDocumentFormView"""
from __future__ import annotations
from app.models import InvoiceKind
from app.services.returns import post_purchase_return
from app.ui.common.document_form import BaseDocumentFormView


class PurchaseReturnInvoiceFormView(BaseDocumentFormView):
    def __init__(self, session, invoice_id: int | None = None, parent=None):
        super().__init__(
            session=session, doc_title="مرتجع شراء", kind=InvoiceKind.PURCHASE_RETURN,
            party_label="المورد", is_customer=False, posting_fn=post_purchase_return,
            ref_prefix="JE-PRET", invoice_id=invoice_id, is_return=True, parent=parent,
        )
