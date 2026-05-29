from odoo import models, fields, api


class TramaSocietyBalance(models.TransientModel):
    _name = 'trama.society.balance'
    _description = 'Saldos por Socio'

    society_type_id = fields.Many2one('trama.society.type', string='Sociedad')
    partner_number = fields.Selection([
        ('1', 'Socio 1'),
        ('2', 'Socio 2'),
        ('3', 'Socio 3'),
        ('4', 'Socio 4'),
    ], string='Socio')
    partner_name = fields.Char(string='Nombre')
    partner_percent = fields.Float(string='%')
    total_gastos = fields.Float(string='Gastos que le corresponden')
    total_depositado = fields.Float(string='Total Depositado')
    total_sweat_equity = fields.Float(string='Sweat Equity')
    saldo_operativo = fields.Float(string='Saldo Operativo')
    saldo_total = fields.Float(string='Saldo Total')

    @api.model
    def calculate_balances(self, society_type_id=None, date_from=None, date_to=None):
        if not society_type_id:
            return []
        society = self.env['trama.society.type'].browse(society_type_id)
        if not society.exists():
            return []

        exp_domain = [('society_type_id', '=', society.id), ('state', '!=', 'cancelled')]
        if date_from:
            exp_domain.append(('date', '>=', date_from))
        if date_to:
            exp_domain.append(('date', '<=', date_to))
        expenses = self.env['trama.society.expense'].search(exp_domain)
        total_expenses = sum(expenses.mapped('amount_total'))

        dep_domain = [('society_type_id', '=', society.id), ('state', '=', 'confirmed')]
        if date_from:
            dep_domain.append(('date', '>=', date_from))
        if date_to:
            dep_domain.append(('date', '<=', date_to))
        deposits = self.env['trama.society.deposit'].search(dep_domain)

        se_domain = [('society_type_id', '=', society.id), ('state', 'in', ('reconocido', 'pendiente'))]
        if date_from:
            se_domain.append(('date', '>=', date_from))
        if date_to:
            se_domain.append(('date', '<=', date_to))
        sweat_equities = self.env['trama.society.sweat.equity'].search(se_domain)

        results = []
        for i in range(1, 5):
            name = getattr(society, f'partner_{i}_name', '')
            pct = getattr(society, f'partner_{i}_percent', 0.0)
            if not name or pct == 0:
                continue
            gastos = total_expenses * (pct / 100.0)
            dep_total = sum(d.amount for d in deposits if d.partner_number == str(i))
            se_total = sum(s.amount for s in sweat_equities if s.partner_number == str(i))
            saldo_op = dep_total - gastos
            saldo_total = dep_total + se_total - gastos
            results.append({
                'partner_number': str(i),
                'partner_name': name,
                'partner_percent': pct,
                'total_gastos': gastos,
                'total_depositado': dep_total,
                'total_sweat_equity': se_total,
                'saldo_operativo': saldo_op,
                'saldo_total': saldo_total,
            })
        return results


class TramaSocietyBalanceWizard(models.TransientModel):
    _name = 'trama.society.balance.wizard'
    _description = 'Wizard Saldo per Socio'

    society_type_id = fields.Many2one('trama.society.type', string='Societat', required=True)
    date_from = fields.Date(string='Des de', default=lambda self: fields.Date.today().replace(month=1, day=1))
    date_to = fields.Date(string='Fins a', default=fields.Date.today)
    line_ids = fields.One2many('trama.society.balance.wizard.line', 'wizard_id', string='Saldos')

    def action_calculate(self):
        self.ensure_one()
        self.line_ids.unlink()
        balances = self.env['trama.society.balance'].calculate_balances(
            society_type_id=self.society_type_id.id,
            date_from=self.date_from,
            date_to=self.date_to,
        )
        lines = []
        for b in balances:
            lines.append((0, 0, {
                'partner_name': b['partner_name'],
                'partner_percent': b['partner_percent'],
                'total_gastos': b['total_gastos'],
                'total_depositado': b['total_depositado'],
                'total_sweat_equity': b['total_sweat_equity'],
                'saldo_operativo': b['saldo_operativo'],
                'saldo_total': b['saldo_total'],
            }))
        self.write({'line_ids': lines})
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class TramaSocietyBalanceWizardLine(models.TransientModel):
    _name = 'trama.society.balance.wizard.line'
    _description = 'Linia de Saldo per Socio'

    wizard_id = fields.Many2one('trama.society.balance.wizard', ondelete='cascade')
    partner_name = fields.Char(string='Socio')
    partner_percent = fields.Float(string='%')
    total_gastos = fields.Float(string='Gastos Corresponents')
    total_depositado = fields.Float(string='Depositat')
    total_sweat_equity = fields.Float(string='Sweat Equity')
    saldo_operativo = fields.Float(string='Saldo Operatiu')
    saldo_total = fields.Float(string='Saldo Total')
