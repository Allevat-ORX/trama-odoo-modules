from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class TramaExpenseBridgeWizard(models.TransientModel):
    """
    Wizard to send multiple hr.expense records to Society module.
    Accessible from hr.expense tree view via 'Enviar a Society' button.
    """
    _name = 'trama.expense.bridge.wizard'
    _description = 'Wizard: Enviar Gastos a Society'

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)

        # Get selected hr.expense IDs from context
        active_ids = self.env.context.get('active_ids', [])
        if active_ids:
            expenses = self.env['hr.expense'].browse(active_ids)
            defaults['hr_expense_ids'] = [(6, 0, active_ids)]
            defaults['expense_count'] = len(active_ids)

            # Try to auto-detect society based on employee
            if len(active_ids) > 0:
                first_expense = expenses[0]
                employee_name = (first_expense.employee_id.name or '').lower()

                # Map employee to society
                if 'erik' in employee_name:
                    marcatek = self.env['trama.society.type'].search(
                        [('code', '=', 'MARCATEK')], limit=1
                    )
                    if marcatek:
                        defaults['society_type_id'] = marcatek.id
                elif 'mendez' in employee_name:
                    global_rent = self.env['trama.society.type'].search(
                        [('code', '=', 'GLOBAL_RENT')], limit=1
                    )
                    if global_rent:
                        defaults['society_type_id'] = global_rent.id
                else:
                    # Default to Global Rent
                    global_rent = self.env['trama.society.type'].search(
                        [('code', '=', 'GLOBAL_RENT')], limit=1
                    )
                    if global_rent:
                        defaults['society_type_id'] = global_rent.id

        return defaults

    hr_expense_ids = fields.Many2many(
        'hr.expense',
        string='Gastos seleccionados',
        required=True
    )

    expense_count = fields.Integer(string='Número de gastos', readonly=True)

    society_type_id = fields.Many2one(
        'trama.society.type',
        string='Sociedad destino',
        required=True,
        help='Sociedad a la que se asignarán estos gastos'
    )

    category_id = fields.Many2one(
        'trama.expense.category',
        string='Categoría por defecto',
        help='Categoría a usar si no se puede detectar automáticamente'
    )

    paid_by = fields.Selection([
        ('company', 'Empresa'),
        ('partner_1', 'Socio 1'),
        ('partner_2', 'Socio 2'),
        ('partner_3', 'Socio 3'),
        ('partner_4', 'Socio 4'),
    ], string='Pagado por', default='partner_1')

    auto_classify = fields.Boolean(
        string='Auto-clasificar por producto',
        default=True,
        help='Intentar detectar categoría automáticamente desde el producto'
    )

    note = fields.Text(string='Notas adicionales')

    def action_send_to_society(self):
        """Send selected expenses to Society"""
        self.ensure_one()

        if not self.hr_expense_ids:
            raise UserError(_('No hay gastos seleccionados'))

        if not self.society_type_id:
            raise UserError(_('Debe seleccionar una Sociedad destino'))

        bridge_model = self.env['trama.expense.bridge']
        created_bridges = []

        for expense in self.hr_expense_ids:
            # Check if already bridged
            existing = bridge_model.search(
                [('hr_expense_id', '=', expense.id)],
                limit=1
            )
            if existing and existing.state != 'cancelled':
                continue

            # Determine category
            category = self.category_id
            auto_classified = False

            if self.auto_classify and expense.product_id and expense.product_id.categ_id:
                detected = self._detect_category(expense.product_id.categ_id)
                if detected:
                    category = detected
                    auto_classified = True

            # Create bridge record
            bridge_vals = {
                'hr_expense_id': expense.id,
                'society_type_id': self.society_type_id.id,
                'category_id': category.id if category else False,
                'paid_by': self._map_employee_to_partner(expense.employee_id),
                'auto_classified': auto_classified,
                'note': self.note or f"Enviado desde wizard por {self.env.user.name}",
            }

            bridge = bridge_model.create(bridge_vals)
            created_bridges.append(bridge)

        # Send all created bridges to society
        for bridge in created_bridges:
            try:
                bridge.action_send_to_society()
            except (UserError, ValidationError) as e:
                # Log error but continue with others
                bridge.message_post(body=f"Error al enviar: {str(e)}")

        # Return success message
        count = len(created_bridges)
        if count == 0:
            raise UserError(_('Los gastos seleccionados ya fueron enviados a Society'))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Éxito',
                'message': f'{count} gasto(s) enviado(s) a Society',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }

    def _detect_category(self, product_categ):
        """Detect expense category from product category"""
        if not product_categ:
            return False

        categ_name = (product_categ.name or '').lower()

        # Mapping - using actual uppercase category codes from system
        mapping = {
            'viaje': 'TECNOLOGIA',
            'hotel': 'TECNOLOGIA',
            'hospedaje': 'TECNOLOGIA',
            'comida': 'OTROS',
            'alimentacion': 'OTROS',
            'alimentación': 'OTROS',
            'transporte': 'TECNOLOGIA',
            'uber': 'TECNOLOGIA',
            'taxi': 'TECNOLOGIA',
            'material': 'OFICINA',
            'oficina': 'OFICINA',
            'papeleria': 'OFICINA',
            'servicio': 'SOFTWARE',
            'telefonia': 'COMUNICACIONES',
            'telefonía': 'COMUNICACIONES',
            'internet': 'COMUNICACIONES',
            'movil': 'COMUNICACIONES',
            'móvil': 'COMUNICACIONES',
            'celular': 'COMUNICACIONES',
            'software': 'SOFTWARE',
            'app': 'SOFTWARE',
            'tecnologia': 'TECNOLOGIA',
            'tecnología': 'TECNOLOGIA',
            'saas': 'SOFTWARE',
            'cloud': 'TECNOLOGIA',
            'nube': 'TECNOLOGIA',
            'nomina': 'NOMINA',
            'nómina': 'NOMINA',
            'sueldo': 'NOMINA',
            'salario': 'NOMINA',
            'marketing': 'MARKETING',
            'ads': 'MARKETING',
            'publicidad': 'MARKETING',
        }

        for keyword, code in mapping.items():
            if keyword in categ_name:
                return self.env['trama.expense.category'].search(
                    [('code', '=', code)], limit=1
                )

        return False

    def _map_employee_to_partner(self, employee):
        """Map employee to society partner"""
        if not employee or not employee.name:
            return 'partner_1'

        emp_name = employee.name.lower()
        society = self.society_type_id

        if not society:
            return 'partner_1'

        # Check each partner
        if society.partner_1_name and society.partner_1_name.lower() in emp_name:
            return 'partner_1'
        if society.partner_2_name and society.partner_2_name.lower() in emp_name:
            return 'partner_2'
        if society.partner_3_name and society.partner_3_name.lower() in emp_name:
            return 'partner_3'
        if society.partner_4_name and society.partner_4_name.lower() in emp_name:
            return 'partner_4'

        return 'partner_1'

    def action_cancel(self):
        """Cancel wizard"""
        return {'type': 'ir.actions.act_window_close'}
