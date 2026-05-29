from datetime import date

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class TramaSocietyExpense(models.Model):
    _name = 'trama.society.expense'
    _description = 'Gasto de Sociedad'
    _order = 'date desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # Estados
    STATE_SELECTION = [
        ('draft', 'Borrador'),
        ('pending', 'Pendiente'),
        ('justified', 'Justificado'),
        ('paid', 'Pagado'),
        ('cancelled', 'Cancelado'),
    ]

    name = fields.Char(string='Concepto', required=True, tracking=True)
    date = fields.Date(string='Fecha', required=True, default=fields.Date.today, tracking=True)

    # Sociedad
    society_type_id = fields.Many2one(
        'trama.society.type',
        string='Sociedad',
        required=True,
        default=lambda self: self._default_society_type(),
        tracking=True
    )

    # Importes
    amount_total = fields.Float(string='Total ($)', required=True, tracking=True)
    amount_paid = fields.Float(string='Pagado ($)', default=0.0, tracking=True)
    amount_pending = fields.Float(string='Pendiente ($)', compute='_compute_amounts', store=True)

    # Distribución dinámica según socios de la sociedad
    # Socio 1
    partner_1_name = fields.Char(string='Socio 1', related='society_type_id.partner_1_name', readonly=True)
    partner_1_percent = fields.Float(string='% Socio 1', related='society_type_id.partner_1_percent', readonly=True)
    partner_1_amount = fields.Float(string='Monto Socio 1', compute='_compute_distribution', store=True)

    # Socio 2
    partner_2_name = fields.Char(string='Socio 2', related='society_type_id.partner_2_name', readonly=True)
    partner_2_percent = fields.Float(string='% Socio 2', related='society_type_id.partner_2_percent', readonly=True)
    partner_2_amount = fields.Float(string='Monto Socio 2', compute='_compute_distribution', store=True)

    # Socio 3
    partner_3_name = fields.Char(string='Socio 3', related='society_type_id.partner_3_name', readonly=True)
    partner_3_percent = fields.Float(string='% Socio 3', related='society_type_id.partner_3_percent', readonly=True)
    partner_3_amount = fields.Float(string='Monto Socio 3', compute='_compute_distribution', store=True)

    # Socio 4
    partner_4_name = fields.Char(string='Socio 4', related='society_type_id.partner_4_name', readonly=True)
    partner_4_percent = fields.Float(string='% Socio 4', related='society_type_id.partner_4_percent', readonly=True)
    partner_4_amount = fields.Float(string='Monto Socio 4', compute='_compute_distribution', store=True)

    # Estado y notas
    state = fields.Selection(STATE_SELECTION, string='Estado', default='draft', tracking=True)
    note = fields.Text(string='Notas')
    reference = fields.Char(string='Referencia/Factura', tracking=True)

    # Comprobantes adjuntos
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'trama_expense_attachment_rel',
        'expense_id',
        'attachment_id',
        string='Comprobantes',
    )

    # Link back to source transaction (Phase 2)
    transaction_id = fields.Many2one(
        'trama.personal.transaction',
        string='Transaccio origen',
        ondelete='set null',
        readonly=True,
        help='Transaccio personal que va generar aquest gasto automaticament.',
    )

    # Categoría según Plan Financiero
    category = fields.Selection([
        ('nomina', 'Nómina (Sueldo CEO)'),
        ('tecnologia', 'Tecnología (AWS, IA, Google, GitHub, Vercel)'),
        ('comunicaciones', 'Comunicaciones (Zadarma, WaSender, SMS, Gamma)'),
        ('oficina', 'Oficina (Servicios, Limpieza)'),
        ('software_prospeccion', 'Software/Prospección (Instantly.ai, Postiz)'),
        ('marketing', 'Marketing (Ads, Contenido Orgánico)'),
        ('otros', 'Otros (Incentivos, Imprevistos)'),
    ], string='Categoría', default='otros')

    # Nueva categoria dinamica (reemplaza el Selection category)
    category_id = fields.Many2one(
        'trama.expense.category',
        string='Categoria',
        tracking=True,
        help='Categoria de gasto segun Plan Financiero. Reemplaza el campo Selection anterior.',
    )

    # invoice_ids, invoice_count, has_invoices → moved to trama_expense_invoice module

    # Quien pagó físicamente (dinámico según socios disponibles)
    paid_by = fields.Selection([
        ('company', 'Empresa'),
        ('partner_1', 'Socio 1'),
        ('partner_2', 'Socio 2'),
        ('partner_3', 'Socio 3'),
        ('partner_4', 'Socio 4'),
    ], string='Pagado por', default='partner_1')

    @api.model
    def _default_society_type(self):
        """Por defecto usar Global Rent"""
        return self.env['trama.society.type'].search([('code', '=', 'GLOBAL_RENT')], limit=1).id

    @api.depends('amount_total', 'amount_paid')
    def _compute_amounts(self):
        for record in self:
            record.amount_pending = record.amount_total - record.amount_paid

    # _compute_invoice_count → moved to trama_expense_invoice module

    @api.depends('amount_total', 'society_type_id')
    def _compute_distribution(self):
        for record in self:
            if record.society_type_id:
                record.partner_1_amount = record.amount_total * (record.partner_1_percent / 100.0)
                record.partner_2_amount = record.amount_total * (record.partner_2_percent / 100.0)
                record.partner_3_amount = record.amount_total * (record.partner_3_percent / 100.0)
                record.partner_4_amount = record.amount_total * (record.partner_4_percent / 100.0)
            else:
                record.partner_1_amount = 0.0
                record.partner_2_amount = 0.0
                record.partner_3_amount = 0.0
                record.partner_4_amount = 0.0

    def action_pending(self):
        self.write({'state': 'pending'})

    def action_justified(self):
        self.write({'state': 'justified'})

    def action_paid(self):
        """Mark expense as paid and update any linked bridges."""
        self.write({'state': 'paid', 'amount_paid': self.amount_total})

        # Update any linked bridges to 'done' state
        bridge_model = self.env['trama.expense.bridge']
        bridges = bridge_model.search([('society_expense_id', '=', self.id)])
        for bridge in bridges:
            bridge.action_update_state()

    def action_cancelled(self):
        self.write({'state': 'cancelled'})

    def action_draft(self):
        self.write({'state': 'draft'})

    def action_view_invoices(self):
        """Open the invoices linked to this expense."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Factures',
            'res_model': 'trama.expense.invoice',
            'view_mode': 'list,form',
            'domain': [('expense_id', '=', self.id)],
            'context': {'default_expense_id': self.id},
        }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_budget_alerts()
        return records

    def write(self, vals):
        res = super().write(vals)
        # Only check alerts if relevant fields changed
        if any(f in vals for f in ('category_id', 'amount_total', 'date', 'state', 'society_type_id')):
            self._check_budget_alerts()
        return res

    def _check_budget_alerts(self):
        """After expense create/write, find or create the budget record
        for this expense's category+month and recompute alerts."""
        BudgetModel = self.env['trama.category.budget']
        for expense in self:
            if not expense.category_id or not expense.date:
                continue
            budget = BudgetModel.get_or_create_for_month(
                category_id=expense.category_id.id,
                month_date=expense.date,
                society_type_id=expense.society_type_id.id if expense.society_type_id else False,
            )
            budget.recompute_and_check_alerts()
