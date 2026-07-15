# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class OnboardingChecklist(models.Model):
    """Per-employee onboarding tracking: docs, NDA, courses, evaluation.

    Plan 10-04 — PIPE-09 / PIPE-10: Visible on hr.employee Onboarding tab.
    """

    _name = "onrentx.onboarding.checklist"
    _description = "Employee Onboarding Checklist"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "employee_id"

    employee_id = fields.Many2one(
        "hr.employee",
        required=True,
        ondelete="cascade",
        string="Employee",
        tracking=True,
    )
    applicant_id = fields.Many2one(
        "hr.applicant", string="Source Applicant", ondelete="set null"
    )

    # Documents (5)
    doc_comprobante = fields.Boolean(string="Comprobante domicilio", tracking=True)
    doc_curp = fields.Boolean(string="CURP", tracking=True)
    doc_ine = fields.Boolean(string="INE (frente + reverso)", tracking=True)
    doc_rfc = fields.Boolean(string="RFC", tracking=True)
    doc_carta_jcf = fields.Boolean(string="Carta Vinculacion JCF", tracking=True)
    docs_complete = fields.Boolean(
        compute="_compute_docs_complete", store=True, string="All Docs Complete"
    )

    # NDA
    nda_sent = fields.Boolean(string="NDA Sent", tracking=True)
    nda_signed = fields.Boolean(string="NDA Signed", tracking=True)
    nda_sign_request_id = fields.Many2one(
        "sign.oca.request",
        string="NDA Sign Request",
        help="Link to sign.oca.request instance for the NDA.",
    )

    # Portal + courses
    portal_user_id = fields.Many2one("res.users", string="Portal User")
    portal_user_created = fields.Boolean(string="Portal User Created", tracking=True)
    course_ids = fields.Many2many(
        "slide.channel",
        "onboarding_checklist_course_rel",
        "checklist_id",
        "channel_id",
        string="Enrolled Courses",
    )
    courses_assigned = fields.Boolean(string="Courses Assigned", tracking=True)
    base_course_completed = fields.Boolean(
        string="Base Course Completed", tracking=True
    )

    # Evaluation
    evaluation_mode = fields.Selection(
        [
            ("trial_week", "Semana de prueba"),
            ("exercises", "Ejercicios practicos"),
            ("both", "Ambos"),
        ],
        compute="_compute_evaluation_mode",
        store=True,
        string="Evaluation Mode",
    )
    evaluation_status = fields.Selection(
        [
            ("pending", "Pendiente"),
            ("in_progress", "En progreso"),
            ("passed", "Aprobado"),
            ("failed", "No aprobado"),
        ],
        default="pending",
        tracking=True,
        string="Evaluation Status",
    )
    evaluation_start_date = fields.Date(string="Evaluation Start")
    evaluation_end_date = fields.Date(string="Evaluation End")

    # Progress
    progress = fields.Float(
        compute="_compute_progress",
        store=True,
        string="Onboarding Progress",
        help="Completion percentage 0-100.",
    )

    @api.depends(
        "doc_comprobante", "doc_curp", "doc_ine", "doc_rfc", "doc_carta_jcf"
    )
    def _compute_docs_complete(self):
        for rec in self:
            rec.docs_complete = all(
                [
                    rec.doc_comprobante,
                    rec.doc_curp,
                    rec.doc_ine,
                    rec.doc_rfc,
                    rec.doc_carta_jcf,
                ]
            )

    @api.depends("employee_id.job_id")
    def _compute_evaluation_mode(self):
        CourseMap = self.env["onrentx.onboarding.course.map"]
        for rec in self:
            mode = False
            if rec.employee_id and rec.employee_id.job_id:
                cmap = CourseMap.search(
                    [("job_id", "=", rec.employee_id.job_id.id), ("active", "=", True)],
                    limit=1,
                )
                mode = cmap.evaluation_mode if cmap else False
            rec.evaluation_mode = mode or "trial_week"

    @api.depends(
        "docs_complete",
        "nda_signed",
        "portal_user_created",
        "courses_assigned",
        "base_course_completed",
        "evaluation_status",
    )
    def _compute_progress(self):
        for rec in self:
            steps = [
                rec.docs_complete,
                rec.nda_signed,
                rec.portal_user_created,
                rec.courses_assigned,
                rec.base_course_completed,
                rec.evaluation_status == "passed",
            ]
            rec.progress = (sum(1 for s in steps if s) / len(steps)) * 100.0
