from odoo import models, fields, api


class TramaSocietySweatEquity(models.Model):
    _name = 'trama.society.sweat.equity'
    _description = 'Sweat Equity - Aportaciones en Especie'
    _order = 'date desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    CONCEPTO_SELECTION = [
        ('renta', 'Renta Oficina'),
        ('comodato', 'Comodato Equipamiento'),
        ('trabajo', 'Trabajo No Remunerado'),
        ('servicios', 'Servicios Profesionales'),
        ('otro', 'Otro'),
    ]

    ESTADO_SELECTION = [
        ('pendiente', 'Pendiente Reconocimiento'),
        ('reconocido', 'Reconocido'),
        ('rechazado', 'Rechazado'),
    ]

    name = fields.Char(string='Concepto', required=True, tracking=True)
    date = fields.Date(string='Fecha Inicio', required=True, tracking=True)
    date_end = fields.Date(string='Fecha Fin', tracking=True)
    period = fields.Char(string='Período', compute='_compute_period', store=True)

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

    # Tipo de concepto
    concepto_type = fields.Selection(CONCEPTO_SELECTION, string='Tipo de Concepto', required=True)

    # Valor
    amount = fields.Float(string='Valor ($)', required=True, tracking=True)
    amount_per_month = fields.Float(string='Valor Mensual ($)', compute='_compute_amount_per_month', store=True)

    # Estado de reconocimiento
    state = fields.Selection(ESTADO_SELECTION, string='Estado', default='pendiente', tracking=True)

    # Notas
    note = fields.Text(string='Notas / Descripción')
    reference = fields.Char(string='Referencia/Inventario', tracking=True)

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

    @api.depends('date', 'date_end')
    def _compute_period(self):
        for record in self:
            if record.date and record.date_end:
                start = record.date.strftime('%b %Y')
                end = record.date_end.strftime('%b %Y')
                record.period = f"{start}–{end}" if start != end else start
            elif record.date:
                record.period = record.date.strftime('%b %Y')
            else:
                record.period = ''

    @api.depends('amount', 'date', 'date_end')
    def _compute_amount_per_month(self):
        for record in self:
            if record.date and record.date_end:
                months = (record.date_end.year - record.date.year) * 12 + (record.date_end.month - record.date.month)
                if months > 0:
                    record.amount_per_month = record.amount / months
                else:
                    record.amount_per_month = record.amount
            else:
                record.amount_per_month = record.amount

    def action_reconocer(self):
        self.write({'state': 'reconocido'})

    def action_rechazar(self):
        self.write({'state': 'rechazado'})
