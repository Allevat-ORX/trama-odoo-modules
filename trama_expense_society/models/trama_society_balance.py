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
    total_sweat_equity = fields.Float(string='Sweat Equity Reconocido')
    saldo = fields.Float(string='Saldo (+ a favor / - debe)')

    @api.model
    def calculate_balances(self, society_type_id=None, date_from=None, date_to=None):
        """Calculate balances for all partners in a society."""
        if not society_type_id:
            return []

        society = self.env['trama.society.type'].browse(society_type_id)
        if not society.exists():
            return []

        # Build expense domain
        exp_domain = [
            ('society_type_id', '=', society.id),
            ('state', '!=', 'cancelled'),
        ]
        if date_from:
            exp_domain.append(('date', '>=', date_from))
        if date_to:
            exp_domain.append(('date', '<=', date_to))

        expenses = self.env['trama.society.expense'].search(exp_domain)
        total_expenses = sum(expenses.mapped('amount_total'))

        # Deposit domain
        dep_domain = [
            ('society_type_id', '=', society.id),
            ('state', '=', 'confirmed'),
        ]
        if date_from:
            dep_domain.append(('date', '>=', date_from))
        if date_to:
            dep_domain.append(('date', '<=', date_to))

        deposits = self.env['trama.society.deposit'].search(dep_domain)

        # Sweat equity domain
        se_domain = [
            ('society_type_id', '=', society.id),
            ('state', '=', 'reconocido'),
        ]
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
            dep_total = sum(
                d.amount for d in deposits
                if d.partner_number == str(i)
            )
            se_total = sum(
                s.amount for s in sweat_equities
                if s.partner_number == str(i)
            )
            saldo = dep_total + se_total - gastos

            results.append({
                'partner_number': str(i),
                'partner_name': name,
                'partner_percent': pct,
                'total_gastos': gastos,
                'total_depositado': dep_total,
                'total_sweat_equity': se_total,
                'saldo': saldo,
            })

        return results
