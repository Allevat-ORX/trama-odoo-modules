# -*- coding: utf-8 -*-
"""hr.employee extension for plan 10-04 / fix pass 10-05.

Adds onboarding_checklist_id, portal user creation, and course auto-enrollment.

Plan 10-05 fixes:
- F4: action_create_portal_user reuses existing res.users on login collision
  (common with JCF re-applicants who already have a portal account).
- F5: hardcoded slide.channel id=10 replaced with ir.config_parameter
  'onrentx.onboarding.base_channel_id' (default 10) with safe name fallback.
"""
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    onboarding_checklist_id = fields.Many2one(
        "onrentx.onboarding.checklist",
        string="Onboarding Checklist",
        copy=False,
    )
    onboarding_progress = fields.Float(
        related="onboarding_checklist_id.progress",
        store=False,
        string="Onboarding Progress %",
    )

    def action_create_portal_user(self):
        """Create a portal res.users for this employee and link it.

        Plan 10-05 F4 fix: reuse existing res.users on login collision.
        JCF candidates who apply a second time often already have a portal
        account from their first onboarding attempt, and Odoo's res.users
        has a unique constraint on 'login' which crashes the whole chain.
        """
        self.ensure_one()
        if self.user_id:
            _logger.info(
                "Plan 10-04: employee %s already has user %s", self.id, self.user_id.id
            )
            return self.user_id

        # Find or create partner
        partner = self.work_contact_id
        if not partner:
            email = self.work_email or self.private_email
            if not email:
                raise UserError(
                    _("Employee %s has no email - cannot create portal user.")
                    % self.name
                )
            partner = (
                self.env["res.partner"]
                .sudo()
                .create(
                    {
                        "name": self.name,
                        "email": email,
                        "phone": self.work_phone or self.mobile_phone,
                        "is_company": False,
                    }
                )
            )
            self.sudo().write({"work_contact_id": partner.id})

        if not partner.email:
            raise UserError(
                _("Employee partner has no email - cannot create portal user.")
            )

        # Plan 10-05 F4: check for existing user with this login (even archived ones)
        User = self.env["res.users"].sudo().with_context(active_test=False)
        portal_group = self.env.ref("base.group_portal")

        existing = User.search([("login", "=", partner.email)], limit=1)
        if existing:
            # Re-activate archived user
            if not existing.active:
                existing.write({"active": True})
                _logger.info(
                    "Plan 10-05 F4: reactivated archived user %s (%s) for employee %s",
                    existing.id,
                    existing.login,
                    self.id,
                )
            # Ensure portal group (but don't kick out if already internal user)
            if portal_group.id not in existing.groups_id.ids and not existing.has_group(
                "base.group_user"
            ):
                existing.write({"groups_id": [(4, portal_group.id)]})
            # Warn (but keep) if existing user has a different partner
            if existing.partner_id.id != partner.id:
                _logger.warning(
                    "Plan 10-05 F4: existing user %s has different partner "
                    "(%s vs %s) — keeping existing partner_id to avoid breaking "
                    "prior relationships",
                    existing.login,
                    existing.partner_id.id,
                    partner.id,
                )
            user = existing
            _logger.info(
                "Plan 10-05 F4: reused existing user %s for employee %s",
                user.id,
                self.id,
            )
        else:
            user = User.with_context(no_reset_password=False).create(
                {
                    "login": partner.email,
                    "partner_id": partner.id,
                    "groups_id": [(6, 0, [portal_group.id])],
                }
            )
            _logger.info(
                "Plan 10-04: portal user %s created for employee %s", user.id, self.id
            )

        self.sudo().write({"user_id": user.id})

        if self.onboarding_checklist_id:
            self.onboarding_checklist_id.sudo().write(
                {
                    "portal_user_id": user.id,
                    "portal_user_created": True,
                }
            )
        return user

    def action_enroll_courses_by_job(self):
        """Enroll employee in courses based on their job's course map.

        Plan 10-05 F5 fix: resolve base channel ID via ir.config_parameter
        instead of hardcoded id=10. Safe fallback via name search if the
        configured ID doesn't exist (e.g. after DB rebuild).
        """
        self.ensure_one()
        if not self.job_id:
            raise UserError(
                _("Employee %s has no job_id - cannot determine courses.") % self.name
            )

        course_map = self.env["onrentx.onboarding.course.map"].search(
            [("job_id", "=", self.job_id.id), ("active", "=", True)], limit=1
        )
        if not course_map:
            _logger.warning(
                "Plan 10-04: no course map for job %s, falling back to Base course only",
                self.job_id.name,
            )
            # Plan 10-05 F5: resolve base channel via config_parameter with name fallback
            ICP = self.env["ir.config_parameter"].sudo()
            try:
                base_id = int(
                    ICP.get_param("onrentx.onboarding.base_channel_id", "10") or "10"
                )
            except (TypeError, ValueError):
                base_id = 10
            base = self.env["slide.channel"].browse(base_id).exists()
            if not base:
                base = self.env["slide.channel"].search(
                    [("name", "ilike", "Onboarding Base")], limit=1
                )
                if base:
                    _logger.info(
                        "Plan 10-05 F5: config_parameter base_channel_id=%s "
                        "doesn't exist, fell back to name search -> %s",
                        base_id,
                        base.id,
                    )
            courses = base
        else:
            courses = course_map.get_all_courses()

        if not courses:
            # Plan 10-05 F5: no-crash fallback when no courses can be resolved
            _logger.warning(
                "Plan 10-05: no courses resolvable for employee %s (job %s) — "
                "skipping enrollment without raising",
                self.id,
                self.job_id.name,
            )
            return self.env["slide.channel"]

        partner = self.work_contact_id or (
            self.user_id.partner_id if self.user_id else False
        )
        if not partner:
            raise UserError(
                _("Employee %s has no partner - create portal user first.")
                % self.name
            )

        SCP = self.env["slide.channel.partner"].sudo()
        for course in courses:
            existing = SCP.search(
                [
                    ("channel_id", "=", course.id),
                    ("partner_id", "=", partner.id),
                ],
                limit=1,
            )
            if not existing:
                SCP.create(
                    {
                        "channel_id": course.id,
                        "partner_id": partner.id,
                        "member_status": "invited",
                        "active": True,
                        "completion": 0,
                        "completed_slides_count": 0,
                    }
                )

        if self.onboarding_checklist_id:
            self.onboarding_checklist_id.sudo().write(
                {
                    "course_ids": [(6, 0, courses.ids)],
                    "courses_assigned": True,
                }
            )
        _logger.info(
            "Plan 10-04: employee %s enrolled in %d courses", self.id, len(courses)
        )
        return courses
