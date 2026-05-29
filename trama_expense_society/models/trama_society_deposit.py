from odoo import models, fields, api


class TramaSocietyDeposit(models.Model):
    _name = 'trama.society.deposit'
    _description = 'Depósito de Socio'
    _order = 'date desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    MEDIO_SELECTION = [
        ('transferencia', 'Transferencia'),
        ('efectivo', 'Efectivo'),
        ('deposito', 'Depósito'),
        ('tarjeta', 'Tarjeta'),
        ('otro', 'Otro'),
    ]

    name = fields.Char(string='Concepto', required=True, tracking=True)
    date = fields.Date(string='Fecha', required=True, default=fields.Date.today, tracking=True)

    # Sociedad
    society_type_id = fields.Many2one(
        'trama.society.type',
        string='Sociedad',
        required=True,
        default=lambda self: self.env['trama.society.expense']._default_society_type(),
        tracking=True
    )

    # Socio (dinámico según socios de la sociedad)
    partner_number = fields.Selection([
        ('1', 'Socio 1'),
        ('2', 'Socio 2'),
        ('3', 'Socio 3'),
        ('4', 'Socio 4'),
    ], string='Número de Socio', required=True)

    partner_name = fields.Char(string='Nombre Socio', compute='_compute_partner_name', store=True)

    # Importe
    amount = fields.Float(string='Monto ($)', required=True, tracking=True)

    # Medio de pago
    medio = fields.Selection(MEDIO_SELECTION, string='Medio de Pago', default='transferencia')

    # Estado
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('confirmed', 'Confirmado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='pending', tracking=True)

    # Notas
    note = fields.Text(string='Notas')
    reference = fields.Char(string='Referencia/Comprobante', tracking=True)

    # Relación con gastos (para conciliación opcional)
    expense_id = fields.Many2one('trama.society.expense', string='Gasto Relacionado', help='Gasto específico que cubre este depósito')

    # Relación con ingresos personales (inverse de trama.personal.income.deposit_ids)
    income_id = fields.Many2one(
        'trama.personal.income',
        string='Ingres Relacionat',
        readonly=True,
        help='Ingres personal que va generar aquest dipòsit (amortització)',
    )

    @api.depends('society_type_id', 'partner_number')
    def _compute_partner_name(self):
        for record in self:
            if record.society_type_id and record.partner_number:
                if record.partner_number == '1':
                    record.partner_name = record.society_type_id.partner_1_name or 'Socio 1'
                elif record.partner_number == '2':
                    record.partner_name = record.society_type_id.partner_2_name or 'Socio 2'
                elif record.partner_number == '3':
                    record.partner_name = record.society_type_id.partner_3_name or 'Socio 3'
                elif record.partner_number == '4':
                    record.partner_name = record.society_type_id.partner_4_name or 'Socio 4'
            else:
                record.partner_name = ''

    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})
