from odoo import models, fields, api


class TramaExpenseCategory(models.Model):
    _name = 'trama.expense.category'
    _description = 'Categoria de Gasto'
    _order = 'sequence, name'

    name = fields.Char(string='Nombre', required=True)
    code = fields.Char(string='Codigo', required=True)
    budget_monthly = fields.Float(
        string='Presupuesto Mensual Default ($)',
        help='Presupuesto mensual por defecto. Se puede sobreescribir por mes en Presupuestos.',
        default=0.0,
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(string='Secuencia', default=10)
    color = fields.Integer(string='Color', default=0)
    note = fields.Text(string='Descripcion')

    # Relacion inversa: gastos en esta categoria
    expense_ids = fields.One2many(
        'trama.society.expense', 'category_id', string='Gastos',
    )
    # Relacion inversa: presupuestos de esta categoria
    budget_ids = fields.One2many(
        'trama.category.budget', 'category_id', string='Presupuestos',
    )

    expense_count = fields.Integer(
        string='Num. Gastos', compute='_compute_expense_count',
    )

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'El codigo de categoria debe ser unico.'),
    ]

    @api.depends('expense_ids')
    def _compute_expense_count(self):
        for record in self:
            record.expense_count = len(record.expense_ids)

    @api.model
    def create_default_categories(self):
        """Seed the 7 expense categories from Plan Financiero."""
        categories = [
            {
                'name': 'Nomina',
                'code': 'NOMINA',
                'budget_monthly': 50000.0,
                'sequence': 10,
                'color': 1,
                'note': 'Sueldo CEO y empleados JCF',
            },
            {
                'name': 'Tecnologia',
                'code': 'TECNOLOGIA',
                'budget_monthly': 6900.0,
                'sequence': 20,
                'color': 2,
                'note': 'AWS, IA (LiteLLM, Ollama), Google Workspace, GitHub, Vercel',
            },
            {
                'name': 'Marketing',
                'code': 'MARKETING',
                'budget_monthly': 10000.0,
                'sequence': 30,
                'color': 3,
                'note': 'Ads, Contenido Organico, ImpactX, Growth',
            },
            {
                'name': 'Oficina',
                'code': 'OFICINA',
                'budget_monthly': 5000.0,
                'sequence': 40,
                'color': 4,
                'note': 'Servicios, Limpieza, Material de oficina',
            },
            {
                'name': 'Software y Prospeccion',
                'code': 'SOFTWARE',
                'budget_monthly': 3000.0,
                'sequence': 50,
                'color': 5,
                'note': 'Instantly.ai, Postiz, herramientas SaaS',
            },
            {
                'name': 'Otros',
                'code': 'OTROS',
                'budget_monthly': 4000.0,
                'sequence': 60,
                'color': 6,
                'note': 'Incentivos, Imprevistos, gastos no clasificados',
            },
            {
                'name': 'Comunicaciones',
                'code': 'COMUNICACIONES',
                'budget_monthly': 1200.0,
                'sequence': 70,
                'color': 7,
                'note': 'Zadarma, WaSender, SMS, Gamma AI',
            },
        ]

        for cat in categories:
            existing = self.search([('code', '=', cat['code'])], limit=1)
            if not existing:
                self.create(cat)

        return True
