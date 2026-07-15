# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class OnboardingCourseMap(models.Model):
    """Maps hr.job positions to onboarding courses and evaluation mode.

    Plan 10-04 — PIPE-10: Per-job onboarding configuration.
    """

    _name = "onrentx.onboarding.course.map"
    _description = "Onboarding Course Map by Job Position"
    _rec_name = "job_id"

    job_id = fields.Many2one(
        "hr.job", required=True, ondelete="cascade", string="Job Position"
    )
    base_course_ids = fields.Many2many(
        "slide.channel",
        "onboarding_map_base_rel",
        "map_id",
        "channel_id",
        string="Base Courses (all employees)",
        help="Courses everyone in this job must take (e.g., Onboarding Base).",
    )
    specific_course_ids = fields.Many2many(
        "slide.channel",
        "onboarding_map_specific_rel",
        "map_id",
        "channel_id",
        string="Job-Specific Courses",
        help="Courses specific to this job position.",
    )
    evaluation_mode = fields.Selection(
        [
            ("trial_week", "Semana de prueba"),
            ("exercises", "Ejercicios practicos"),
            ("both", "Ambos"),
        ],
        default="trial_week",
        required=True,
        string="Evaluation Mode",
    )
    trial_week_days = fields.Integer(
        default=5, string="Trial Week Days", help="Duration of trial week in days."
    )
    exercise_description = fields.Html(
        string="Exercise Description",
        help="HTML description of practical exercises for evaluation mode=exercises.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "job_unique",
            "unique(job_id)",
            "Each job can only have one onboarding course map.",
        ),
    ]

    def get_all_courses(self):
        """Return the union of base + specific courses for this map."""
        self.ensure_one()
        return self.base_course_ids | self.specific_course_ids
