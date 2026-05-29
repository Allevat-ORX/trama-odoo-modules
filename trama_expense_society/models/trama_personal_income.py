from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class TramaPersonalIncome(models.Model):
    _name = 'trama.personal.income'
    _description = 'Ingres Personal'
    _order = 'date desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    SOURCE_TYPE_SELECTION = [
        ('salary_onrentx', 'Nomina OnRentX'),
        ('salary_marcatek', 'Nomina MARCATEK'),
        ('freelance', 'Freelance'),
        ('other', 'Otro'),
    ]

    STATE_SELECTION = [
        ('draft', 'Borrador'),
        ('confirmed', 'Confirmado'),
        ('amortized', 'Amortizado'),
    ]

    # --- Core fields ---
    name = fields.Char(
        string='Concepte',
        required=True,
        tracking=True,
    )
    date = fields.Date(
        string='Data',
        required=True,
        default=fields.Date.today,
        tracking=True,
    )
    amount = fields.Float(
        string='Import ($)',
        required=True,
        tracking=True,
    )

    # --- Source ---
    source_type = fields.Selection(
        SOURCE_TYPE_SELECTION,
        string="Tipus d'ingres",
        required=True,
        tracking=True,
    )

    # --- Society that pays ---
    society_type_id = fields.Many2one(
        'trama.society.type',
        string='Societat que paga',
        tracking=True,
    )

    # --- Amortization ---
    is_amortizable = fields.Boolean(
        string='Amortitzable',
        default=False,
        tracking=True,
        help='Si es marca, es generara un deposit damortitzacio en confirmar',
    )
    amortization_partner_number = fields.Selection(
        [('1', '1'), ('2', '2'), ('3', '3'), ('4', '4')],
        string='Numero de soci que amortitza',
        tracking=True,
    )
    amortization_percent = fields.Float(
        string='Percentatge damortitzacio (%)',
        tracking=True,
        help='Percentatge del soci (ex: 51% per Aleix a OnRentX)',
    )
    amount_amortized = fields.Float(
        string='Import amortitzat',
        compute='_compute_amortized',
        store=True,
        help='Import que samortitza = ingres * percentatge del soci',
    )

    # --- Linked deposits ---
    deposit_ids = fields.One2many(
        'trama.society.deposit',
        'income_id',
        string='Deposits generats',
        readonly=True,
    )
    deposit_count = fields.Integer(
        string='Num. Deposits',
        compute='_compute_deposit_count',
    )

    # --- State ---
    state = fields.Selection(
        STATE_SELECTION,
        string='Estat',
        default='draft',
        tracking=True,
    )

    # --- Notes ---
    note = fields.Text(string='Notes')

    # -------------------------------------------------------
    # Computed
    # -------------------------------------------------------
    @api.depends('amount', 'amortization_percent')
    def _compute_amortized(self):
        for rec in self:
            if rec.is_amortizable and rec.amount and rec.amortization_percent:
                rec.amount_amortized = rec.amount * (rec.amortization_percent / 100.0)
            else:
                rec.amount_amortized = 0.0

    @api.depends('deposit_ids')
    def _compute_deposit_count(self):
        for rec in self:
            rec.deposit_count = len(rec.deposit_ids)

    # -------------------------------------------------------
    # Action: Confirm income (creates amortization deposit if applicable)
    # -------------------------------------------------------
    def action_confirm(self):
        """Confirm income and auto-generate amortization deposit if applicable."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError('Nomes es pot confirmar un ingress en estat borrador.')

            if rec.is_amortizable:
                if not rec.amortization_partner_number:
                    raise UserError('Si lamortitzacio es activa, cal indicar el numero de soci.')
                if not rec.amortization_percent:
                    raise UserError('Si lamortitzacio es activa, cal indicar el percentatge.')
                if not rec.society_type_id:
                    raise UserError('Cal indicar la societat que paga per generar lamortitzacio.')

                # Create amortization deposit
                deposit_vals = rec._prepare_amortization_deposit_vals()
                deposit = self.env['trama.society.deposit'].create(deposit_vals)
                rec.state = 'amortized'
            else:
                rec.state = 'confirmed'

        return True

    def _prepare_amortization_deposit_vals(self):
        """Build vals for amortization deposit."""
        self.ensure_one()
        return {
            'name': f'Amortitzacio: {self.name}',
            'date': self.date,
            'society_type_id': self.society_type_id.id,
            'partner_number': self.amortization_partner_number,
            'amount': self.amount_amortized,
            'medio': 'transferencia',
            'income_id': self.id,
            'state': 'confirmed',
            'note': f'Amortitzacio automatica dari {self.amount} ({self.amortization_percent}%)',
        }

    # -------------------------------------------------------
    # Action: Reset to draft
    # -------------------------------------------------------
    def action_reset_to_draft(self):
        """Reset income to draft state, delete linked deposits."""
        for rec in self:
            if rec.state == 'amortized':
                # Delete linked deposits before resetting
                if rec.deposit_ids:
                    rec.deposit_ids.unlink()
            rec.state = 'draft'

    # -------------------------------------------------------
    # View action: Open linked deposits
    # -------------------------------------------------------
    def action_view_deposits(self):
        """Open the deposits linked to this income."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Deposits generats',
            'res_model': 'trama.society.deposit',
            'view_mode': 'list,form',
            'domain': [('income_id', '=', self.id)],
            'context': {'default_income_id': self.id},
        }
