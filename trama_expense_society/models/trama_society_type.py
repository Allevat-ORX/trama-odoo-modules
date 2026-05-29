from odoo import models, fields, api


class TramaSocietyType(models.Model):
    _name = 'trama.society.type'
    _description = 'Tipo de Sociedad'
    _order = 'name'

    name = fields.Char(string='Nombre Sociedad', required=True)
    code = fields.Char(string='Código', required=True)

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'El código de sociedad debe ser único.'),
    ]
    active = fields.Boolean(default=True)

    # Socios con sus porcentajes (hasta 4 socios para flexibilidad)
    partner_1_name = fields.Char(string='Socio 1', default='Aleix')
    partner_1_percent = fields.Float(string='% Socio 1', default=0.0)

    partner_2_name = fields.Char(string='Socio 2', default='')
    partner_2_percent = fields.Float(string='% Socio 2', default=0.0)

    partner_3_name = fields.Char(string='Socio 3', default='')
    partner_3_percent = fields.Float(string='% Socio 3', default=0.0)

    partner_4_name = fields.Char(string='Socio 4', default='')
    partner_4_percent = fields.Float(string='% Socio 4', default=0.0)

    note = fields.Text(string='Notas')

    # Campos computados para acceso rápido
    partner_count = fields.Integer(string='Número de Socios', compute='_compute_partner_count')

    @api.depends('partner_1_name', 'partner_2_name', 'partner_3_name', 'partner_4_name')
    def _compute_partner_count(self):
        for record in self:
            count = 0
            if record.partner_1_name: count += 1
            if record.partner_2_name: count += 1
            if record.partner_3_name: count += 1
            if record.partner_4_name: count += 1
            record.partner_count = count

    @api.constrains('partner_1_percent', 'partner_2_percent', 'partner_3_percent', 'partner_4_percent')
    def _check_percentages(self):
        for record in self:
            total = (record.partner_1_percent + record.partner_2_percent +
                    record.partner_3_percent + record.partner_4_percent)
            if total != 100.0 and total != 0:
                raise models.ValidationError(f'La suma de porcentajes debe ser 100%. Actual: {total}%')

    @api.model
    def create_default_societies(self):
        """Crear las sociedades configuradas"""
        societies = [
            {
                'name': 'Global Rent X',
                'code': 'GLOBAL_RENT',
                'partner_1_name': 'Aleix',
                'partner_1_percent': 51.0,
                'partner_2_name': 'Méndez',
                'partner_2_percent': 9.0,
                'partner_3_name': 'Enrique',
                'partner_3_percent': 40.0,
                'note': 'Operadora OnRentX México',
            },
            {
                'name': 'MARCATEK',
                'code': 'MARCATEK',
                'partner_1_name': 'Aleix',
                'partner_1_percent': 50.0,
                'partner_2_name': 'Erik',
                'partner_2_percent': 50.0,
                'note': 'SAT y Prospectus con Erik Mata',
            },
            {
                'name': 'Trama Instalaciones',
                'code': 'TRAMA',
                'partner_1_name': 'Aleix',
                'partner_1_percent': 100.0,
                'note': 'Instalaciones MEP (solo Aleix)',
            },
        ]

        for soc in societies:
            existing = self.search([('code', '=', soc['code'])], limit=1)
            if not existing:
                self.create(soc)

        return True
