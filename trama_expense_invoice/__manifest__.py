{
    'name': 'Trama - Invoice Tracking for Society Expenses',
    'version': '1.0.0',
    'category': 'Accounting',
    'summary': 'Tracking de factures de proveïdors per a gastos de societat',
    'description': """
        Mòdul per gestionar el tracking de factures de proveïdors:
        - Registre de factures rebudes/pendents per cada expense
        - Estat de factura (Borrador, Pendent, Rebuda, Cancel·lada)
        - Adjunts PDF/XML
        - Resum de factures per report
    """,
    'author': 'Trama Instalaciones',
    'website': 'https://trama.instalaciones',
    'depends': ['trama_expense_society'],
    'data': [
        'security/ir.model.access.csv',
        'views/trama_expense_invoice_views.xml',
    ],
    'demo': [],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
