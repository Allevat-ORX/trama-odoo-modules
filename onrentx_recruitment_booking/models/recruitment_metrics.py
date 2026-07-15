# -*- coding: utf-8 -*-
"""
Recruitment Metrics Model
Aggregates pipeline data for dashboard and reporting
"""
from odoo import models, fields, api
import odoo.tools as tools
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class RecruitmentMetrics(models.Model):
    _name = 'recruitment.metrics'
    _description = 'Recruitment Pipeline Metrics'
    _order = 'date desc, stage'

    # Core fields
    date = fields.Date(
        string='Date',
        required=True,
        default=fields.Date.today,
        help='Date of the metric record'
    )

    stage = fields.Selection([
        ('application', 'Application'),
        ('survey', 'Survey Completed'),
        ('prescreening', 'Pre-screening'),
        ('jcf_validation', 'JCF Validation'),
        ('interview', 'Interview'),
        ('contract', 'Contract'),
        ('hired', 'Hired'),
    ], string='Pipeline Stage', required=True)

    job_id = fields.Many2one(
        'hr.job',
        string='Job Position',
        help='Related job position (optional)'
    )

    # Metrics
    count = fields.Integer(
        string='Candidate Count',
        default=0,
        help='Number of candidates at this stage'
    )

    avg_time_days = fields.Float(
        string='Avg Time (Days)',
        digits=(6, 2),
        help='Average days to reach this stage from application'
    )

    conversion_rate = fields.Float(
        string='Conversion Rate (%)',
        digits=(5, 2),
        help='Percentage of candidates converting to this stage'
    )

    # Computed fields for reporting
    total_applications = fields.Integer(
        string='Total Applications',
        compute='_compute_totals',
        store=True
    )

    # SQL View for efficient aggregation
    @api.model
    def init(self):
        """Create materialized view for fast dashboard queries"""
        # Removed incorrect line
        tools.drop_view_if_exists(self.env.cr, 'recruitment_metrics_view')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW recruitment_metrics_view AS
            SELECT
                ha.id as id,
                DATE(ha.create_date) as application_date,
                ha.job_id as job_id,
                ha.stage_id as stage_id,
                ha.wa_chat_state as chat_state,
                ha.survey_score as survey_score,
                ha.wa_chat_score as wa_chat_score,
                ha.prescreening_eval as prescreening_eval,
                ha.create_date as create_date,
                ha.date_closed as date_closed,
                ha.booking_start as booking_start
            FROM hr_applicant ha
            WHERE ha.active = true
        """)

    @api.depends('date', 'stage', 'job_id')
    def _compute_totals(self):
        """Compute total applications for context"""
        for record in self:
            if record.date:
                record.total_applications = self.env['hr.applicant'].search_count([
                    ('create_date', '>=', record.date),
                    ('create_date', '<', record.date + timedelta(days=1))
                ])
            else:
                record.total_applications = 0

    @api.model
    def compute_daily_metrics(self, target_date=None):
        """
        Compute metrics for a specific date or yesterday.
        Called by cron job daily.
        """
        if target_date is None:
            target_date = fields.Date.today() - timedelta(days=1)

        _logger.info(f"Computing recruitment metrics for {target_date}")

        # Check if already computed for this date
        existing = self.search([('date', '=', target_date)], limit=1)
        if existing:
            _logger.info(f"Metrics already computed for {target_date}, skipping")
            return True

        stages = ['application', 'survey', 'prescreening', 'jcf_validation', 'interview', 'contract', 'hired']
        total_apps = 0
        stage_counts = {}
        stage_times = {}

        # Get all applicants created on target date
        applicants = self.env['hr.applicant'].search([
            ('create_date', '>=', target_date),
            ('create_date', '<', target_date + timedelta(days=1))
        ])

        total_apps = len(applicants)

        for stage in stages:
            if stage == 'application':
                # Application stage = all applicants
                count = total_apps
                avg_time = 0.0
            else:
                # Count candidates who reached this stage
                count, avg_time = self._count_at_stage(applicants, stage, target_date)

            stage_counts[stage] = count
            stage_times[stage] = avg_time

            # Calculate conversion rate
            conversion_rate = 0.0
            if total_apps > 0:
                conversion_rate = (count / total_apps) * 100

            # Create metrics record
            self.create({
                'date': target_date,
                'stage': stage,
                'count': count,
                'avg_time_days': avg_time,
                'conversion_rate': conversion_rate,
            })

        _logger.info(f"Completed metrics computation for {target_date}")
        return True

    def _count_at_stage(self, applicants, stage, target_date):
        """
        Count applicants who reached a specific stage by target_date.
        Returns: (count, avg_time_days)
        """
        count = 0
        total_days = 0.0

        for applicant in applicants:
            reached, days = self._applicant_reached_stage(applicant, stage, target_date)
            if reached:
                count += 1
                if days is not None:
                    total_days += days

        avg_time = total_days / count if count > 0 else 0.0
        return count, avg_time

    def _applicant_reached_stage(self, applicant, stage, target_date):
        """
        Check if applicant reached a stage by target_date.
        Returns: (reached: bool, days: float or None)
        """
        if stage == 'survey':
            # Survey completed if survey_score exists
            if applicant.survey_score and applicant.survey_score > 0:
                # Survey completion date is not stored, use create_date + estimate
                return True, 1.0
            return False, None

        elif stage == 'prescreening':
            # Pre-screening completed if wa_chat_state is evaluated
            if applicant.wa_chat_state in ['approved', 'rejected', 'completed']:
                if applicant.prescreening_eval:
                    # Parse prescreening_eval which contains date
                    try:
                        eval_date = datetime.strptime(applicant.prescreening_eval, '%Y-%m-%d').date()
                        days = (eval_date - applicant.create_date.date()).days
                        return True, float(days)
                    except:
                        pass
                return True, None
            return False, None

        elif stage == 'jcf_validation':
            # JCF validation if comprobante uploaded
            if applicant.x_comprobante_filename or applicant.jcf_attachment_id:
                return True, 2.0
            return False, None

        elif stage == 'interview':
            # Interview if booking confirmed
            if applicant.booking_start and applicant.booking_start.date() <= target_date:
                days = (applicant.booking_start.date() - applicant.create_date.date()).days
                return True, float(days)
            return False, None

        elif stage == 'contract':
            # Contract stage
            if applicant.contract_sent or applicant.stage_id.name == 'Contract':
                return True, 5.0
            return False, None

        elif stage == 'hired':
            # Hired if date_closed exists
            if applicant.date_closed and applicant.date_closed.date() <= target_date:
                days = (applicant.date_closed.date() - applicant.create_date.date()).days
                return True, float(days)
            return False, None

        return False, None

    @api.model
    def get_funnel_data(self, date_from=None, date_to=None, job_id=None):
        """
        Get funnel visualization data for dashboard.
        Returns dict with stages and counts.
        """
        if date_from is None:
            date_from = fields.Date.today() - timedelta(days=30)
        if date_to is None:
            date_to = fields.Date.today()

        domain = [
            ('date', '>=', date_from),
            ('date', '<=', date_to)
        ]
        if job_id:
            domain.append(('job_id', '=', job_id))

        # Aggregate by stage
        self.env.cr.execute("""
            SELECT stage, SUM(count) as total_count,
                   AVG(avg_time_days) as avg_time,
                   AVG(conversion_rate) as avg_conversion
            FROM recruitment_metrics
            WHERE date >= %s AND date <= %s
            GROUP BY stage
            ORDER BY CASE stage
                WHEN 'application' THEN 1
                WHEN 'survey' THEN 2
                WHEN 'prescreening' THEN 3
                WHEN 'jcf_validation' THEN 4
                WHEN 'interview' THEN 5
                WHEN 'contract' THEN 6
                WHEN 'hired' THEN 7
            END
        """, (date_from, date_to))

        result = self.env.cr.fetchall()
        funnel_data = []
        stages_labels = {
            'application': 'Applications',
            'survey': 'Survey Completed',
            'prescreening': 'Pre-screened',
            'jcf_validation': 'JCF Validated',
            'interview': 'Interviewed',
            'contract': 'Contract Sent',
            'hired': 'Hired'
        }

        for row in result:
            stage, count, avg_time, conversion = row
            funnel_data.append({
                'stage': stage,
                'label': stages_labels.get(stage, stage),
                'count': int(count or 0),
                'avg_time_days': round(avg_time or 0, 2),
                'conversion_rate': round(conversion or 0, 2)
            })

        return {
            'date_from': date_from,
            'date_to': date_to,
            'stages': funnel_data,
            'total_applications': funnel_data[0]['count'] if funnel_data else 0
        }

    @api.model
    def get_time_distribution(self, job_id=None, days_back=30):
        """
        Get time distribution histogram data.
        """
        date_from = fields.Date.today() - timedelta(days=days_back)
        date_to = fields.Date.today()

        domain = [
            ('date', '>=', date_from),
            ('date', '<=', date_to)
        ]
        if job_id:
            domain.append(('job_id', '=', job_id))

        metrics = self.search(domain)

        # Calculate bucketed times
        buckets = {
            '0-3 days': 0,
            '4-7 days': 0,
            '8-14 days': 0,
            '15-30 days': 0,
            '30+ days': 0
        }

        for m in metrics:
            if m.avg_time_days is None:
                continue
            days = m.avg_time_days
            if days <= 3:
                buckets['0-3 days'] += m.count
            elif days <= 7:
                buckets['4-7 days'] += m.count
            elif days <= 14:
                buckets['8-14 days'] += m.count
            elif days <= 30:
                buckets['15-30 days'] += m.count
            else:
                buckets['30+ days'] += m.count

        return buckets
