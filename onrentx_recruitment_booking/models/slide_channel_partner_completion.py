# -*- coding: utf-8 -*-
"""slide.channel.partner extension for plan 10-04 truth #9 (fixed in 10-05 as F2).

Detects base course completion and triggers:
- onrentx.onboarding.checklist.base_course_completed = True
- evaluation_status = 'in_progress' (if currently 'pending')
- evaluation_start_date = today
- Fires the mail_template_evaluation_kickoff mail.template

Plan 10-04 promised this behaviour in its truths list but never implemented
the hook on slide.channel.partner. This module delivers the missing piece.
"""
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class SlideChannelPartner(models.Model):
    _inherit = "slide.channel.partner"

    def write(self, vals):
        # Capture pre-state per record so we only fire on the *transition* to complete
        was_incomplete = {}
        if "completion" in vals or "member_status" in vals:
            for scp in self:
                was_incomplete[scp.id] = (
                    (scp.completion or 0) < 100
                    and scp.member_status != "completed"
                )

        result = super().write(vals)

        # Fire hook for any record that just transitioned to complete
        for scp in self:
            if not was_incomplete.get(scp.id):
                continue
            completed_now = (
                (scp.completion or 0) >= 100 or scp.member_status == "completed"
            )
            if not completed_now:
                continue
            try:
                scp._trigger_onboarding_completion_hook()
            except Exception as exc:
                _logger.exception(
                    "Plan 10-05 F2: onboarding completion hook failed for "
                    "slide.channel.partner %s: %s",
                    scp.id,
                    exc,
                )
        return result

    def _trigger_onboarding_completion_hook(self):
        """If this course is the Base onboarding course for some employee,
        mark the checklist + kickoff evaluation."""
        self.ensure_one()

        # Only act if this channel is the configured Base onboarding course
        ICP = self.env["ir.config_parameter"].sudo()
        try:
            base_id = int(
                ICP.get_param("onrentx.onboarding.base_channel_id", "10") or "10"
            )
        except (TypeError, ValueError):
            base_id = 10

        if self.channel_id.id != base_id:
            # Not the base course — future: track per-course completion separately
            return

        # Find checklists for employees whose partner matches this scp.partner_id
        Employee = self.env["hr.employee"].sudo()
        employees = Employee.search([("work_contact_id", "=", self.partner_id.id)])
        if not employees:
            # Try by email fallback
            if self.partner_id.email:
                employees = Employee.search(
                    [
                        "|",
                        ("work_email", "=", self.partner_id.email),
                        ("private_email", "=", self.partner_id.email),
                    ]
                )

        if not employees:
            _logger.info(
                "Plan 10-05 F2: base course completed for partner %s but no "
                "matching employee found — skipping evaluation kickoff",
                self.partner_id.id,
            )
            return

        Checklist = self.env["onrentx.onboarding.checklist"].sudo()
        for employee in employees:
            checklist = employee.onboarding_checklist_id
            if not checklist:
                # Employee is enrolled in the base course but has no checklist
                # (e.g. manual enrollment) — skip without erroring
                continue

            vals = {"base_course_completed": True}
            if checklist.evaluation_status == "pending":
                vals["evaluation_status"] = "in_progress"
                vals["evaluation_start_date"] = fields.Date.today()

            checklist.write(vals)
            _logger.info(
                "Plan 10-05 F2: checklist %s marked base_course_completed "
                "(employee %s, evaluation_status=%s)",
                checklist.id,
                employee.id,
                checklist.evaluation_status,
            )

            # Fire evaluation kickoff email
            try:
                template = self.env.ref(
                    "onrentx_recruitment_booking.mail_template_evaluation_kickoff",
                    raise_if_not_found=False,
                )
                if template and (employee.work_email or employee.private_email):
                    template.sudo().send_mail(employee.id, force_send=False)
                    _logger.info(
                        "Plan 10-05 F2: evaluation kickoff mail queued for employee %s",
                        employee.id,
                    )
            except Exception as exc:
                _logger.exception(
                    "Plan 10-05 F2: evaluation kickoff mail failed for employee %s: %s",
                    employee.id,
                    exc,
                )
