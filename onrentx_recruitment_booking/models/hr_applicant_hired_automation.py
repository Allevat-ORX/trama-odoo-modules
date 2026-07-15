# -*- coding: utf-8 -*-
"""hr.applicant extension for plan 10-04.

Detects transition to a hired_stage and auto-creates hr.employee +
instantiates the onboarding checklist.
"""
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class HrApplicant(models.Model):
    _inherit = "hr.applicant"

    def write(self, vals):
        # Detect stage change to hired_stage BEFORE super
        applicants_to_trigger = self.env["hr.applicant"]
        if "stage_id" in vals:
            new_stage = self.env["hr.recruitment.stage"].browse(vals["stage_id"])
            if new_stage and new_stage.hired_stage:
                for app in self:
                    # Only trigger if applicant was not already in a hired stage
                    if not app.stage_id.hired_stage:
                        applicants_to_trigger |= app

        result = super().write(vals)

        for app in applicants_to_trigger:
            try:
                app._trigger_hired_automation()
            except Exception as exc:
                _logger.exception(
                    "Plan 10-04: hired automation failed for applicant %s: %s",
                    app.id,
                    exc,
                )
                app.message_post(
                    body=_(
                        "Hired automation failed: %s. See server logs for details."
                    )
                    % exc
                )
        return result

    def _trigger_hired_automation(self):
        """Auto-create employee + checklist when applicant reaches hired_stage."""
        self.ensure_one()

        # Skip if candidate already has an employee linked
        candidate = self.candidate_id
        if candidate and candidate.employee_id:
            _logger.info(
                "Plan 10-04: applicant %s already has employee %s, skipping",
                self.id,
                candidate.employee_id.id,
            )
            self._ensure_checklist(candidate.employee_id)
            return

        # Native Odoo method - same as the UI button "Create Employee"
        self.create_employee_from_applicant()

        # Refresh candidate and get employee
        self.invalidate_recordset()
        candidate = self.candidate_id
        if not (candidate and candidate.employee_id):
            _logger.warning(
                "Plan 10-04: create_employee_from_applicant did not link employee for applicant %s",
                self.id,
            )
            return

        self._ensure_checklist(candidate.employee_id)

    def _ensure_checklist(self, employee):
        """Create an onboarding checklist if the employee doesn't have one yet."""
        if employee.onboarding_checklist_id:
            return employee.onboarding_checklist_id
        checklist = (
            self.env["onrentx.onboarding.checklist"]
            .sudo()
            .create(
                {
                    "employee_id": employee.id,
                    "applicant_id": self.id,
                }
            )
        )
        employee.sudo().write({"onboarding_checklist_id": checklist.id})
        _logger.info(
            "Plan 10-04: onboarding checklist %s created for employee %s",
            checklist.id,
            employee.id,
        )
        return checklist
