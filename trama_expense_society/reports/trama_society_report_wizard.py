from odoo import models, fields, api
from dateutil.relativedelta import relativedelta


class TramaSocietyReportWizard(models.TransientModel):
    _name = 'trama.society.report.wizard'
    _description = 'Asistente Estado de Cuenta'

    society_type_id = fields.Many2one(
        'trama.society.type',
        string='Sociedad',
        required=True,
    )
    date_from = fields.Date(
        string='Desde',
        required=True,
        default=lambda self: fields.Date.today().replace(day=1, month=1),
    )
    date_to = fields.Date(
        string='Hasta',
        required=True,
        default=fields.Date.today,
    )

    def action_generate_report(self):
        self.ensure_one()
        data = self._prepare_report_data()
        return self.env.ref(
            'trama_expense_society.action_report_estado_cuenta'
        ).report_action(self, data=data)

    def _compute_budget_data(self):
        """Query trama.category.budget for month range."""
        self.ensure_one()
        # Build month ranges between date_from and date_to
        months = []
        current = self.date_from.replace(day=1)
        while current <= self.date_to:
            months.append(current)
            current = (current + relativedelta(months=1)).replace(day=1)

        domain = [
            ('month', '>=', self.date_from.replace(day=1)),
            ('month', '<=', self.date_to),
        ]
        if self.society_type_id:
            domain.append(('society_type_id', '=', self.society_type_id.id))
        else:
            domain.append(('society_type_id', '=', False))

        budgets = self.env['trama.category.budget'].search(domain)

        result = []
        for b in budgets:
            result.append({
                'category': b.category_id.name,
                'budgeted': b.amount_budgeted,
                'spent': b.amount_spent,
                'remaining': b.amount_remaining,
                'percent': b.percent_used,
            })
        return sorted(result, key=lambda x: x['category'])

    def _compute_invoice_summary(self):
        """Resum status de factures."""
        self.ensure_one()
        domain = [
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ]
        if self.society_type_id:
            domain.append(('society_type_id', '=', self.society_type_id.id))

        expenses = self.env['trama.society.expense'].search(domain)
        total = len(expenses)
        with_invoice = sum(1 for e in expenses if e.invoice_ids)

        return {
            'total': total,
            'with_invoice': with_invoice,
            'without_invoice': total - with_invoice,
            'percent_justified': (with_invoice / total * 100) if total > 0 else 0.0,
        }

    def _prepare_report_data(self):
        self.ensure_one()
        stype = self.society_type_id

        # Collect partner info dynamically
        partners = []
        for i in range(1, 5):
            name = getattr(stype, f'partner_{i}_name', '') or ''
            pct = getattr(stype, f'partner_{i}_percent', 0.0)
            if name and pct > 0:
                partners.append({'number': str(i), 'name': name, 'percent': pct})

        # Expenses
        expenses = self.env['trama.society.expense'].search([
            ('society_type_id', '=', stype.id),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('state', '!=', 'cancelled'),
        ], order='date asc')

        # Deposits
        deposits = self.env['trama.society.deposit'].search([
            ('society_type_id', '=', stype.id),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('state', '=', 'confirmed'),
        ])

        # Sweat Equity
        sweat = self.env['trama.society.sweat.equity'].search([
            ('society_type_id', '=', stype.id),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('state', '=', 'reconocido'),
        ])

        total_expenses = sum(expenses.mapped('amount_total'))

        # Build per-partner data
        partner_data = []
        for p in partners:
            num = p['number']
            pct = p['percent']
            gastos = total_expenses * (pct / 100.0)
            dep = sum(d.amount for d in deposits if d.partner_number == num)
            se = sum(s.amount for s in sweat if s.partner_number == num)
            saldo = dep + se - gastos
            partner_data.append({
                'name': p['name'],
                'percent': pct,
                'gastos': gastos,
                'depositos': dep,
                'sweat_equity': se,
                'saldo': saldo,
            })

        # Expense detail
        state_labels = dict(self.env['trama.society.expense']._fields['state'].selection)
        detalle = []
        for exp in expenses:
            row = {
                'date': str(exp.date),
                'name': exp.name,
                'total': exp.amount_total,
                'category': exp.category,
                'state': state_labels.get(exp.state, exp.state),
                'partners': [],
            }
            for p in partners:
                i = int(p['number'])
                row['partners'].append({
                    'name': p['name'],
                    'amount': getattr(exp, f'partner_{i}_amount', 0.0),
                })
            detalle.append(row)

        return {
            'society': stype.name,
            'date_from': str(self.date_from),
            'date_to': str(self.date_to),
            'partners': partner_data,
            'detalle_gastos': detalle,
            'total_expenses': total_expenses,
            'budget_vs_actual': self._compute_budget_data(),
            'invoice_status': self._compute_invoice_summary(),
        }

    def action_generate_consolidated_report(self):
        """Generate consolidated report for all societies."""
        self.ensure_one()
        data = self._prepare_consolidated_data()
        return self.env.ref(
            'trama_expense_society.action_report_estado_cuenta_consolidat'
        ).report_action(self, data=data)

    def _prepare_consolidated_data(self):
        """Prepare data for consolidated report (all societies)."""
        self.ensure_one()
        society_types = self.env['trama.society.type'].search([])

        societies_data = []
        grand_total = 0.0

        for stype in society_types:
            # Reuse logic from _prepare_report_data for each society
            partners = []
            for i in range(1, 5):
                name = getattr(stype, f'partner_{i}_name', '') or ''
                pct = getattr(stype, f'partner_{i}_percent', 0.0)
                if name and pct > 0:
                    partners.append({'number': str(i), 'name': name, 'percent': pct})

            expenses = self.env['trama.society.expense'].search([
                ('society_type_id', '=', stype.id),
                ('date', '>=', self.date_from),
                ('date', '<=', self.date_to),
                ('state', '!=', 'cancelled'),
            ], order='date asc')

            deposits = self.env['trama.society.deposit'].search([
                ('society_type_id', '=', stype.id),
                ('date', '>=', self.date_from),
                ('date', '<=', self.date_to),
                ('state', '=', 'confirmed'),
            ])

            sweat = self.env['trama.society.sweat.equity'].search([
                ('society_type_id', '=', stype.id),
                ('date', '>=', self.date_from),
                ('date', '<=', self.date_to),
                ('state', '=', 'reconocido'),
            ])

            total_expenses = sum(expenses.mapped('amount_total'))
            grand_total += total_expenses

            partner_data = []
            for p in partners:
                num = p['number']
                pct = p['percent']
                gastos = total_expenses * (pct / 100.0)
                dep = sum(d.amount for d in deposits if d.partner_number == num)
                se = sum(s.amount for s in sweat if s.partner_number == num)
                saldo = dep + se - gastos
                partner_data.append({
                    'name': p['name'],
                    'percent': pct,
                    'gastos': gastos,
                    'depositos': dep,
                    'sweat_equity': se,
                    'saldo': saldo,
                })

            societies_data.append({
                'society_name': stype.name,
                'total_expenses': total_expenses,
                'partners': partner_data,
            })

        return {
            'date_from': str(self.date_from),
            'date_to': str(self.date_to),
            'societies': societies_data,
            'grand_total': grand_total,
        }
