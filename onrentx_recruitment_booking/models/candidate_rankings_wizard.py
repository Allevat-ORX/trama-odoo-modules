# -*- coding: utf-8 -*-
"""
Wizard for generating candidate rankings report with date filter
"""
from odoo import models, fields, api
from datetime import datetime, timedelta


class CandidateRankingsWizard(models.TransientModel):
    _name = 'candidate.rankings.wizard'
    _description = 'Candidate Rankings Report Wizard'

    date_from = fields.Date(
        string='From Date',
        required=True,
        default=lambda self: fields.Date.today() - timedelta(days=30)
    )

    date_to = fields.Date(
        string='To Date',
        required=True,
        default=fields.Date.today
    )

    job_id = fields.Many2one(
        'hr.job',
        string='Job Position',
        help='Filter by specific job position (optional)'
    )

    min_score = fields.Float(
        string='Minimum Score',
        default=0.0,
        help='Only show candidates with score above this threshold'
    )

    @api.model
    def default_get(self, fields_list):
        defaults = super(CandidateRankingsWizard, self).default_get(fields_list)
        # If called from a job, pre-fill it
        if self.env.context.get('active_model') == 'hr.job':
            defaults['job_id'] = self.env.context.get('active_id')
        return defaults

    def action_generate_report(self):
        """Generate the PDF report"""
        self.ensure_one()

        # Prepare data for report
        data = {
            'ids': [self.job_id.id] if self.job_id else [],
            'model': 'hr.job',
            'form': {
                'date_from': self.date_from,
                'date_to': self.date_to,
                'job_id': self.job_id.id if self.job_id else False,
                'min_score': self.min_score,
            }
        }

        return self.env.ref(
            'onrentx_recruitment_booking.action_report_candidate_rankings'
        ).report_action(self.job_id or self.env['hr.job'], data=data)

    def get_ranked_candidates(self, job_id=None, date_from=None, date_to=None, min_score=0.0):
        """Get candidates sorted by overall score"""
        domain = [
            ('create_date', '>=', date_from),
            ('create_date', '<=', date_to),
        ]

        if job_id:
            domain.append(('job_id', '=', job_id))

        applicants = self.env['hr.applicant'].search(domain)

        candidates = []
        for applicant in applicants:
            overall = applicant.get_ranking_score() if hasattr(applicant, 'get_ranking_score') else 0.0

            if overall >= min_score:
                candidates.append({
                    'id': applicant.id,
                    'name': applicant.partner_name or applicant.name,
                    'stage': applicant.stage_id.name if applicant.stage_id else 'New',
                    'survey_score': applicant.survey_score or 0.0,
                    'wa_chat_score': applicant.wa_chat_score or 0.0,
                    'overall_score': overall,
                    'days_to_screen': applicant.days_to_screen,
                    'days_to_interview': applicant.days_to_interview,
                    'days_to_hire': applicant.days_to_hire,
                })

        # Sort by overall_score desc, then wa_chat_score desc
        candidates.sort(key=lambda x: (x['overall_score'], x['wa_chat_score']), reverse=True)

        # Add rank
        for i, candidate in enumerate(candidates, 1):
            candidate['rank'] = i

        return candidates

    def get_statistics(self, candidates):
        """Calculate summary statistics"""
        if not candidates:
            return {
                'total': 0,
                'avg_survey': 0.0,
                'avg_chat': 0.0,
                'avg_days_interview': 0.0,
            }

        total = len(candidates)
        survey_scores = [c['survey_score'] for c in candidates if c['survey_score']]
        chat_scores = [c['wa_chat_score'] for c in candidates if c['wa_chat_score']]
        days = [c['days_to_interview'] for c in candidates if c['days_to_interview']]

        return {
            'total': total,
            'avg_survey': sum(survey_scores) / len(survey_scores) if survey_scores else 0.0,
            'avg_chat': sum(chat_scores) / len(chat_scores) if chat_scores else 0.0,
            'avg_days_interview': sum(days) / len(days) if days else 0.0,
        }

    def get_stage_distribution(self, candidates):
        """Get distribution by stage"""
        if not candidates:
            return []

        from collections import Counter
        stages = Counter(c['stage'] for c in candidates)
        total = len(candidates)

        return [
            {
                'name': stage,
                'count': count,
                'percentage': round((count / total) * 100, 1)
            }
            for stage, count in stages.most_common()
        ]
