# -*- coding: utf-8 -*-
"""hr.job extension for plan 10-04: adds course_map_id lookup."""
from odoo import api, fields, models


class HrJob(models.Model):
    _inherit = "hr.job"

    course_map_id = fields.Many2one(
        "onrentx.onboarding.course.map",
        string="Onboarding Course Map",
        compute="_compute_course_map_id",
        store=False,
    )

    def _compute_course_map_id(self):
        CourseMap = self.env["onrentx.onboarding.course.map"]
        for job in self:
            cmap = CourseMap.search([("job_id", "=", job.id)], limit=1)
            job.course_map_id = cmap.id if cmap else False
