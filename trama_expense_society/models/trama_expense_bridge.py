from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class TramaExpenseBridge(models.Model):
    """
    Bridge model to link hr.expense with trama.society.expense
    Allows using native Odoo mobile app + OCR for capture,
    then sending to Society module for partner distribution.
    """
    _name = 'trama.expense.bridge'
    _description = 'Puente hr.expense → Society Expense'
    _order = 'create_date desc'
    _inherit = ['mail.thread']

    STATE_SELECTION = [
        ('draft', 'Pendiente'),
        ('sent', 'Enviado a Society'),
        ('done', 'Procesado'),
        ('cancelled', 'Cancelado'),
    ]

    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    state = fields.Selection(STATE_SELECTION, string='Estado', default='draft', tracking=True)

    # Link to hr.expense
    hr_expense_id = fields.Many2one(
        'hr.expense',
        string='Gasto hr.expense',
        required=True,
        readonly=True,
        ondelete='cascade',
        index=True,
        help='Gasto original capturado desde la app móvil Odoo'
    )

    _sql_constraints = [
        ('hr_expense_unique', 'unique(hr_expense_id)',
         'Este gasto ya fue enviado a Society. Use el bridge existente.')
    ]

    # Source data from hr.expense
    hr_employee_id = fields.Many2one(
        'hr.employee',
        string='Empleado',
        related='hr_expense_id.employee_id',
        readonly=True,
        store=True
    )
    hr_product_id = fields.Many2one(
        'product.product',
        string='Producto',
        related='hr_expense_id.product_id',
        readonly=True,
        store=True
    )
    hr_date = fields.Date(
        string='Fecha gasto',
        related='hr_expense_id.date',
        readonly=True,
        store=True
    )

    # NEW: link to hr.expense.sheet (added for sheet bridge)
    sheet_id = fields.Many2one(
        'hr.expense.sheet',
        string='Reporte hr.expense',
        ondelete='set null',
        index=True,
        help='Reporte de gasto (hr.expense.sheet) al que pertenece este bridge'
    )

    hr_total_amount = fields.Monetary(
        string='Monto total',
        related='hr_expense_id.total_amount',
        readonly=True,
        store=True
    )
    hr_currency_id = fields.Many2one(
        'res.currency',
        string='Moneda',
        related='hr_expense_id.currency_id',
        readonly=True,
        store=True
    )

    # Target Society Expense (created after sending)
    society_expense_id = fields.Many2one(
        'trama.society.expense',
        string='Gasto Society',
        readonly=True,
        help='Gasto creado en el módulo Society'
    )

    # Configuration for Society
    society_type_id = fields.Many2one(
        'trama.society.type',
        string='Sociedad destino',
        required=True,
        default=lambda self: self._default_society_type(),
        help='Sociedad a la que se asignará este gasto'
    )

    category_id = fields.Many2one(
        'trama.expense.category',
        string='Categoría',
        help='Categoría del gasto (auto-detectada desde producto)'
    )

    # Partner payment mapping
    paid_by = fields.Selection([
        ('company', 'Empresa'),
        ('partner_1', 'Socio 1'),
        ('partner_2', 'Socio 2'),
        ('partner_3', 'Socio 3'),
        ('partner_4', 'Socio 4'),
    ], string='Pagado por', default='partner_1',
       help='Quién pagó físicamente el gasto')

    # Notes
    note = fields.Text(string='Notas')

    # Auto-classification flag
    auto_classified = fields.Boolean(string='Auto-clasificado', default=False,
                                     help='Indica si la categoría se detectó automáticamente')

    currency_id = fields.Many2one('res.currency', related='hr_currency_id')

    @api.model
    def _default_society_type(self):
        """Default to Global Rent (most common)"""
        return self.env['trama.society.type'].search([('code', '=', 'GLOBAL_RENT')], limit=1).id

    @api.depends('hr_expense_id', 'hr_employee_id')
    def _compute_name(self):
        for record in self:
            if record.hr_expense_id:
                record.name = f"Bridge: {record.hr_expense_id.name[:50]}"
            else:
                record.name = "Nuevo Bridge"

    @api.onchange('hr_product_id')
    def _onchange_product_auto_classify(self):
        """Auto-classify based on product category"""
        if self.hr_product_id and self.hr_product_id.categ_id:
            category = self._map_product_category(self.hr_product_id.categ_id)
            if category:
                self.category_id = category
                self.auto_classified = True

    def _map_product_category(self, product_categ):
        """Map product category to expense category using real system codes."""
        if not product_categ:
            return self._get_default_category()

        categ_name = (product_categ.name or '').lower()

        # Mapping rules - keywords to actual system category codes (UPPERCASE)
        mapping = {
            # Viajes y hospedaje → TECNOLOGIA (o crear categoria VIAJE en el futuro)
            'viaje': 'TECNOLOGIA',
            'hotel': 'TECNOLOGIA',
            'hospedaje': 'TECNOLOGIA',
            # Comidas y alimentación → OTROS (o crear categoria COMIDAS)
            'comida': 'OTROS',
            'alimentacion': 'OTROS',
            'alimentación': 'OTROS',
            # Transporte → TECNOLOGIA (uber/taxi como apps)
            'transporte': 'TECNOLOGIA',
            'uber': 'TECNOLOGIA',
            'taxi': 'TECNOLOGIA',
            # Material de oficina → OFICINA
            'material': 'OFICINA',
            'oficina': 'OFICINA',
            'papeleria': 'OFICINA',
            # Servicios varios → SOFTWARE
            'servicio': 'SOFTWARE',
            'servicios': 'SOFTWARE',
            # Telecomunicaciones → COMUNICACIONES
            'telefonia': 'COMUNICACIONES',
            'telefonía': 'COMUNICACIONES',
            'internet': 'COMUNICACIONES',
            'movil': 'COMUNICACIONES',
            'móvil': 'COMUNICACIONES',
            'celular': 'COMUNICACIONES',
            # Software y tecnología
            'software': 'SOFTWARE',
            'app': 'SOFTWARE',
            'aplicacion': 'SOFTWARE',
            'aplicación': 'SOFTWARE',
            'tecnologia': 'TECNOLOGIA',
            'tecnología': 'TECNOLOGIA',
            'saas': 'SOFTWARE',
            'cloud': 'TECNOLOGIA',
            'nube': 'TECNOLOGIA',
            # Nómina
            'nomina': 'NOMINA',
            'nómina': 'NOMINA',
            'sueldo': 'NOMINA',
            'salario': 'NOMINA',
            # Marketing
            'marketing': 'MARKETING',
            'ads': 'MARKETING',
            'publicidad': 'MARKETING',
        }

        # Find matching category
        for keyword, expense_code in mapping.items():
            if keyword in categ_name:
                category = self.env['trama.expense.category'].search(
                    [('code', '=', expense_code)], limit=1
                )
                if category:
                    return category

        # Default to 'OTROS' category
        return self._get_default_category()

    def _get_default_category(self):
        """Get default 'OTROS' category"""
        category = self.env['trama.expense.category'].search(
            [('code', '=', 'OTROS')], limit=1
        )
        return category

    def action_send_to_society(self):
        """Send this hr.expense to Society module"""
        self.ensure_one()

        # Check access rights
        self.check_access_rights('write')
        self.check_access_rule('write')

        if self.state != 'draft':
            raise UserError(_(
                'Solo se pueden enviar gastos en estado Pendiente.'
            ))

        if not self.society_type_id:
            raise UserError(_(
                'Debe seleccionar una Sociedad destino.'
            ))

        # Get the employee's partner for paid_by mapping
        paid_by_mapping = self._get_employee_partner_mapping()

        # Create Society Expense
        society_vals = {
            'name': self.hr_expense_id.name or 'Gasto desde app móvil',
            'date': self.hr_date or fields.Date.today(),
            'society_type_id': self.society_type_id.id,
            'amount_total': self.hr_total_amount or 0.0,
            'category_id': self.category_id.id if self.category_id else False,
            'paid_by': paid_by_mapping.get(self.paid_by, 'partner_1'),
            'reference': f"hr.expense: {self.hr_expense_id.name}",
            'state': 'draft',
            'note': self.note or f"Enviado desde hr.expense por {self.hr_employee_id.name or 'N/A'}",
        }

        society_expense = self.env['trama.society.expense'].create(society_vals)

        # Update state
        self.write({
            'state': 'sent',
            'society_expense_id': society_expense.id,
        })

        # Post message
        self.message_post(
            body=f"Gasto enviado a Society: {society_expense.name} (ID: {society_expense.id})"
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Éxito',
                'message': f'Gasto enviado a Society: {society_expense.name}',
                'type': 'success',
                'sticky': False,
            }
        }

    def _get_employee_partner_mapping(self):
        """Map employee to society partner based on name"""
        self.ensure_one()

        employee_name = (self.hr_employee_id.name or '').lower()

        # Check if employee name matches any partner
        society = self.society_type_id
        mapping = {}

        if society.partner_1_name and society.partner_1_name.lower() in employee_name:
            mapping[self.paid_by] = 'partner_1'
        elif society.partner_2_name and society.partner_2_name.lower() in employee_name:
            mapping[self.paid_by] = 'partner_2'
        elif society.partner_3_name and society.partner_3_name.lower() in employee_name:
            mapping[self.paid_by] = 'partner_3'
        elif society.partner_4_name and society.partner_4_name.lower() in employee_name:
            mapping[self.paid_by] = 'partner_4'
        else:
            # Default to partner_1
            mapping[self.paid_by] = 'partner_1'

        return mapping

    def action_cancel(self):
        """Cancel the bridge"""
        self.write({'state': 'cancelled'})

    def action_reset_draft(self):
        """Reset to draft (only if not yet sent or done)"""
        for record in self:
            if record.society_expense_id:
                raise UserError(_(
                    'No se puede resetear: ya existe un gasto creado en Society.'
                ))
            if record.state == 'done':
                raise UserError(_(
                    'No se puede resetear: el gasto ya fue procesado completamente.'
                ))
        self.write({'state': 'draft'})

    def action_update_state(self):
        """Update bridge state based on society_expense_id state.
        Called when society.expense transitions to 'paid'."""
        for record in self:
            if record.society_expense_id and record.society_expense_id.state == 'paid':
                if record.state != 'done':
                    record.write({'state': 'done'})
                    record.message_post(body='Gasto marcado como "Procesado" automáticamente (society.expense pagado).')

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-classify on creation if product provided"""
        records = super().create(vals_list)
        for record in records:
            if record.hr_product_id and not record.category_id:
                category = record._map_product_category(record.hr_product_id.categ_id)
                if category:
                    record.category_id = category
                    record.auto_classified = True
        return records
