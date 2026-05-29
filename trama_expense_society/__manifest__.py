{
    'name': 'Trama - Gastos de Sociedad',
    'version': '1.7.1',
    'category': 'Accounting',
    'summary': 'Gestion de gastos y aportaciones entre socios con presupuestos por categoria y transacciones personales',
    'description': """
        Modulo para gestionar gastos de sociedad con distribucion automatica:
        - Sociedad 50/50 (inicial Aleix-Enrique)
        - Sociedad 51/40/9 (nueva: Aleix/Enrique/Mendez)
        - Calculo automatico de saldos por socio
        - Sweat Equity (aportaciones en especie)
        - Categorias de gasto con presupuestos mensuales
        - Alertas al 80%% y 100%% del presupuesto (Odoo + WhatsApp)
        - Reportes: Estado de Cuenta, Reporte de Gastos
        - Transacciones personales con clasificacion y generacion automatica de gastos
        - Importacion CSV de estados de cuenta bancarios con deteccion de duplicados
        - Puente hr.expense: Captura desde app movil Odoo con OCR nativo
        - Auto-clasificacion de gastos por categoria de producto
    """,
    'author': 'Trama Instalaciones',
    'website': 'https://trama.instalaciones',
    'depends': ['base', 'account', 'mail', 'hr_expense'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'views/trama_society_type_views.xml',
        'views/trama_society_expense_views.xml',
        'views/trama_society_deposit_views.xml',
        'views/trama_society_sweat_equity_views.xml',
        'reports/trama_society_report.xml',
        'data/expense_category_data.xml',
        'views/trama_expense_category_views.xml',
        'views/trama_category_budget_views.xml',
        'views/trama_personal_transaction_views.xml',
        'views/trama_personal_income_views.xml',
        'views/trama_bank_import_batch_views.xml',
        'wizard/trama_transaction_classify_wizard.xml',
        'wizard/trama_bank_import_wizard.xml',
        'views/trama_society_balance_views.xml',
        'wizard/trama_expense_bridge_wizard.xml',
        'wizard/trama_expense_sheet_bridge_wizard.xml',
        'views/trama_expense_bridge_views.xml',
        'views/trama_society_menu.xml',
    ],
    'demo': [],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'post_init_hook': 'post_init_hook',
}
