import logging
import requests
from datetime import date
from dateutil.relativedelta import relativedelta

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# WaSender config for budget alerts
WASENDER_TOKEN = '6c6a6a8d7bccb9473f86457b33abb46303e8e36f2108d507eae789ec4fcde6fc'
WASENDER_SESSION = 'soporte'
WASENDER_ALERT_PHONE = '523385263456'  # Aleix


class TramaCategoryBudget(models.Model):
    _name = 'trama.category.budget'
    _description = 'Presupuesto Mensual por Categoria'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'month desc, category_id'
    _rec_name = 'display_name'

    category_id = fields.Many2one(
        'trama.expense.category',
        string='Categoria',
        required=True,
        ondelete='cascade',
    )
    society_type_id = fields.Many2one(
        'trama.society.type',
        string='Sociedad',
        help='Dejar vacio para presupuesto global (todas las sociedades).',
    )
    month = fields.Date(
        string='Mes',
        required=True,
        help='Primer dia del mes. Ej: 2026-05-01 = Mayo 2026.',
    )
    amount_budgeted = fields.Float(
        string='Presupuesto ($)',
        required=True,
        help='Si es 0, se usa el default de la categoria.',
    )

    # Computed fields
    amount_spent = fields.Float(
        string='Gastado ($)',
        compute='_compute_amounts',
        store=True,
    )
    amount_remaining = fields.Float(
        string='Restante ($)',
        compute='_compute_amounts',
        store=True,
    )
    percent_used = fields.Float(
        string='% Usado',
        compute='_compute_amounts',
        store=True,
    )
    is_over_budget = fields.Boolean(
        string='Sobre Presupuesto',
        compute='_compute_amounts',
        store=True,
    )
    is_near_budget = fields.Boolean(
        string='Cerca del Limite (>=80%)',
        compute='_compute_amounts',
        store=True,
    )

    display_name = fields.Char(
        string='Nombre', compute='_compute_display_name', store=True,
    )

    # Track if alerts were already sent to avoid duplicates
    alert_80_sent = fields.Boolean(string='Alerta 80% Enviada', default=False)
    alert_100_sent = fields.Boolean(string='Alerta 100% Enviada', default=False)

    _sql_constraints = [
        (
            'category_society_month_uniq',
            'unique(category_id, society_type_id, month)',
            'Solo puede haber un presupuesto por categoria, sociedad y mes.',
        ),
    ]

    @api.depends('category_id.name', 'month', 'society_type_id.name')
    def _compute_display_name(self):
        for record in self:
            month_str = record.month.strftime('%b %Y') if record.month else '??'
            soc_str = f' [{record.society_type_id.name}]' if record.society_type_id else ''
            record.display_name = f'{record.category_id.name} - {month_str}{soc_str}'

    @api.depends('category_id', 'society_type_id', 'month', 'amount_budgeted')
    def _compute_amounts(self):
        """Compute spent from actual expenses in the same category+month."""
        for record in self:
            if not record.category_id or not record.month:
                record.amount_spent = 0.0
                record.amount_remaining = record.amount_budgeted
                record.percent_used = 0.0
                record.is_over_budget = False
                record.is_near_budget = False
                continue

            # Determine effective budget: use record amount, or fall back to category default
            effective_budget = record.amount_budgeted or record.category_id.budget_monthly

            # Calculate month range
            month_start = record.month.replace(day=1)
            month_end = month_start + relativedelta(months=1, days=-1)

            # Build domain: category + date range + not cancelled
            domain = [
                ('category_id', '=', record.category_id.id),
                ('date', '>=', month_start),
                ('date', '<=', month_end),
                ('state', '!=', 'cancelled'),
            ]
            # If society-specific budget, filter by society too
            if record.society_type_id:
                domain.append(('society_type_id', '=', record.society_type_id.id))

            expenses = self.env['trama.society.expense'].search(domain)
            total_spent = sum(expenses.mapped('amount_total'))

            record.amount_spent = total_spent
            record.amount_remaining = effective_budget - total_spent
            if effective_budget > 0:
                record.percent_used = (total_spent / effective_budget) * 100.0
            else:
                record.percent_used = 0.0
            record.is_over_budget = record.percent_used >= 100.0
            record.is_near_budget = record.percent_used >= 80.0 and record.percent_used < 100.0

    def recompute_and_check_alerts(self):
        """Called after expense create/write to recompute and fire alerts."""
        self._compute_amounts()

        for record in self:
            # 80% alert: Odoo activity only
            if record.is_near_budget and not record.alert_80_sent:
                record._send_near_budget_activity()
                record.alert_80_sent = True

            # 100% alert: Odoo activity + WhatsApp
            if record.is_over_budget and not record.alert_100_sent:
                record._send_over_budget_activity()
                record._send_whatsapp_alert()
                record.alert_100_sent = True

    def _send_near_budget_activity(self):
        """Create an Odoo activity warning that budget is at 80%+."""
        self.ensure_one()
        self.activity_schedule(
            'mail.mail_activity_data_warning',
            summary=f'Presupuesto al {self.percent_used:.0f}% - {self.category_id.name}',
            note=(
                f'La categoria "{self.category_id.name}" ha alcanzado '
                f'el {self.percent_used:.0f}% del presupuesto mensual.\n'
                f'Gastado: ${self.amount_spent:,.2f} / '
                f'Presupuesto: ${self.amount_budgeted or self.category_id.budget_monthly:,.2f}'
            ),
            user_id=self.env.ref('base.user_admin').id,
        )
        _logger.info(
            'Budget alert 80%%: category=%s month=%s percent=%.1f%%',
            self.category_id.code, self.month, self.percent_used,
        )

    def _send_over_budget_activity(self):
        """Create an Odoo activity for 100% budget exceeded."""
        self.ensure_one()
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=f'PRESUPUESTO EXCEDIDO - {self.category_id.name}',
            note=(
                f'La categoria "{self.category_id.name}" ha EXCEDIDO '
                f'el presupuesto mensual.\n'
                f'Gastado: ${self.amount_spent:,.2f} / '
                f'Presupuesto: ${self.amount_budgeted or self.category_id.budget_monthly:,.2f}\n'
                f'Excedido por: ${abs(self.amount_remaining):,.2f}'
            ),
            user_id=self.env.ref('base.user_admin').id,
        )
        _logger.info(
            'Budget alert 100%%: category=%s month=%s percent=%.1f%%',
            self.category_id.code, self.month, self.percent_used,
        )

    def _send_whatsapp_alert(self):
        """Send WhatsApp message via WaSender API when budget is 100%+."""
        self.ensure_one()
        message = (
            f'*ALERTA PRESUPUESTO EXCEDIDO*\n\n'
            f'Categoria: {self.category_id.name}\n'
            f'Mes: {self.month.strftime("%B %Y") if self.month else "??"}\n'
            f'Gastado: ${self.amount_spent:,.2f}\n'
            f'Presupuesto: ${self.amount_budgeted or self.category_id.budget_monthly:,.2f}\n'
            f'Excedido por: ${abs(self.amount_remaining):,.2f}\n'
            f'% Usado: {self.percent_used:.0f}%'
        )
        try:
            resp = requests.post(
                'https://app.wasender.net/api/send-message',
                headers={
                    'Authorization': f'Bearer {WASENDER_TOKEN}',
                    'Content-Type': 'application/json',
                },
                json={
                    'sessionId': WASENDER_SESSION,
                    'to': WASENDER_ALERT_PHONE,
                    'text': message,
                },
                timeout=10,
            )
            _logger.info(
                'WaSender budget alert sent: status=%s category=%s',
                resp.status_code, self.category_id.code,
            )
        except Exception as e:
            _logger.warning('WaSender budget alert failed: %s', e)

    @api.model
    def get_or_create_for_month(self, category_id, month_date, society_type_id=False):
        """Find or create a budget record for the given category+month.

        If no record exists, creates one using the category's default budget.
        month_date can be any date in that month -- it will be normalized to day=1.
        """
        month_start = month_date.replace(day=1)
        domain = [
            ('category_id', '=', category_id),
            ('month', '=', month_start),
        ]
        if society_type_id:
            domain.append(('society_type_id', '=', society_type_id))
        else:
            domain.append(('society_type_id', '=', False))

        budget = self.search(domain, limit=1)
        if not budget:
            category = self.env['trama.expense.category'].browse(category_id)
            budget = self.create({
                'category_id': category_id,
                'society_type_id': society_type_id,
                'month': month_start,
                'amount_budgeted': category.budget_monthly,
            })
        return budget
