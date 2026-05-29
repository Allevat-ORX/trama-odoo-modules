from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class TramaExpenseSheetBridgeWizard(models.TransientModel):
    """
    Wizard to send an approved hr.expense.sheet to Society module.
    Creates ONE trama.society.expense for the whole report,
    plus bridge records per expense line for traceability.
    """
    _name = 'trama.expense.sheet.bridge.wizard'
    _description = 'Wizard: Enviar Reporte a Society'

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if active_id:
            sheet = self.env['hr.expense.sheet'].browse(active_id)
            defaults['hr_expense_sheet_id'] = active_id
            defaults['amount_total'] = sheet.total_amount or 0.0
            defaults['expense_count'] = len(sheet.expense_line_ids)
            # Try to detect society from first employee
            if sheet.expense_line_ids:
                first_emp = sheet.expense_line_ids[0].employee_id
                if first_emp and first_emp.name:
                    emp_name = first_emp.name.lower()
                    society = False
                    if 'erik' in emp_name:
                        society = self.env['trama.society.type'].search(
                            [('code', '=', 'MARCATEK')], limit=1
                        )
                    elif 'mendez' in emp_name:
                        society = self.env['trama.society.type'].search(
                            [('code', '=', 'GLOBAL_RENT')], limit=1
                        )
                    else:
                        society = self.env['trama.society.type'].search(
                            [('code', '=', 'GLOBAL_RENT')], limit=1
                        )
                    if society:
                        defaults['society_type_id'] = society.id
        return defaults

    hr_expense_sheet_id = fields.Many2one(
        'hr.expense.sheet',
        string='Reporte de Gastos',
        required=True,
        readonly=True,
    )
    amount_total = fields.Float(string='Monto Total', readonly=True)
    expense_count = fields.Integer(string='Número de Gastos', readonly=True)
    society_type_id = fields.Many2one(
        'trama.society.type',
        string='Sociedad destino',
        required=True,
        help='Sociedad a la que se asignará este reporte'
    )
    category_id = fields.Many2one(
        'trama.expense.category',
        string='Categoría',
        help='Categoría del gasto para el registro de Society'
    )
    paid_by = fields.Selection([
        ('company', 'Empresa'),
        ('partner_1', 'Socio 1'),
        ('partner_2', 'Socio 2'),
        ('partner_3', 'Socio 3'),
        ('partner_4', 'Socio 4'),
    ], string='Pagado por', default='partner_1')
    note = fields.Text(string='Notas adicionales')

    def action_send_to_society(self):
        """Send the approved expense report to Society"""
        self.ensure_one()
        sheet = self.hr_expense_sheet_id
        if not sheet:
            raise UserError(_('No se encontró el reporte de gastos'))

        # Require approved or later state
        if sheet.state not in ('approve', 'post', 'done'):
            raise UserError(_(
                'El reporte debe estar aprobado (estado: Aprobado, Contabilizado o Hecho)'
            ))

        # Create single Society Expense for the whole sheet
        society_vals = {
            'name': f"Reporte: {sheet.name}",
            'date': fields.Date.today(),
            'society_type_id': self.society_type_id.id,
            'amount_total': sheet.total_amount or 0.0,
            'category_id': self.category_id.id if self.category_id else False,
            'paid_by': self.paid_by,
            'reference': f"hr.expense.sheet: {sheet.name}",
            'state': 'draft',
            'note': self.note or f"Enviado desde reporte hr.expense por {self.env.user.name}",
        }
        society_expense = self.env['trama.society.expense'].create(society_vals)

        # Create / update bridge records per expense line
        bridge_model = self.env['trama.expense.bridge']
        linked_count = 0
        for expense in sheet.expense_line_ids:
            existing = bridge_model.search(
                [('hr_expense_id', '=', expense.id)],
                limit=1
            )
            if existing:
                # Re-link to this sheet and mark done
                existing.write({
                    'sheet_id': sheet.id,
                    'society_expense_id': society_expense.id,
                    'society_type_id': self.society_type_id.id,
                    'state': 'done',
                })
            else:
                bridge_vals = {
                    'hr_expense_id': expense.id,
                    'sheet_id': sheet.id,
                    'society_type_id': self.society_type_id.id,
                    'category_id': self.category_id.id if self.category_id else False,
                    'paid_by': self.paid_by,
                    'state': 'done',
                    'society_expense_id': society_expense.id,
                    'note': f"Bridge desde reporte {sheet.name}",
                }
                bridge_model.create(bridge_vals)
            linked_count += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Éxito',
                'message': f'Reporte enviado a Society: {society_expense.name} ({linked_count} gastos)',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }

    def action_cancel(self):
        """Close wizard without action"""
        return {'type': 'ir.actions.act_window_close'}
