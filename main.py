"""
نقطة الدخول — نطاق v1 مبسّط جداً: يفتح أول عميل موجود بالسجل المركزي،
أو ينشئ عميل تجريبي جديد بشجرة حسابات كاملة لو السجل فاضي.

شاشة اختيار/إنشاء عميل حقيقية بالواجهة لسه ما بُنيت — هذا placeholder
مقصود لتشغيل باقي الشاشات وتجربتها فعلياً أولاً.
"""

import sys
from PySide6.QtWidgets import QApplication

from app.db import get_registry_session, open_company_db, create_company
from app.services.chart_of_accounts_template import create_default_chart_of_accounts
from app.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)

    registry = get_registry_session()
    from app.db import CompanyRecord
    company = registry.query(CompanyRecord).first()

    if company is None:
        company = create_company(registry, name="شركة تجريبية", db_filename="demo.db", base_currency="SYP")

    session = open_company_db(company.db_filename)
    from app.models import Account
    if session.query(Account).count() == 0:
        create_default_chart_of_accounts(session)

    window = MainWindow(session)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
