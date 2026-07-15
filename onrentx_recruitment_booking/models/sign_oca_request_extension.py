# -*- coding: utf-8 -*-
"""sign.oca.request extension for plan 10-04 / fix pass 10-05.

Detects NDA signing completion and triggers post-NDA automation:
- Mark checklist nda_signed
- Create portal user (with collision handling)
- Enroll employee in courses by job
- Send welcome WhatsApp with credentials

Plan 10-05 F1 fix: accept both 'signed' and '2_signed' state literals.
On VM .80 (verified 2026-04-10) the actual OCA selection is:
    [('0_sent', 'Sent'), ('1_draft', 'Draft'),
     ('2_signed', 'Signed'), ('3_cancel', 'Cancelled')]
The 'signed' branch is defensive dead code in current deployment but
future-proofs against OCA API changes.

Plan 10-05 F3 fix: if portal user creation fails, skip enrollment + WA
instead of continuing to a partial onboarding state.
"""
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


# Plan 10-05 F1: belt-and-suspenders — support both OCA naming conventions.
# Canonical value on VM .80 is '2_signed'. 'signed' is kept for resilience.
SIGNED_STATES = ("signed", "2_signed")


class SignOcaRequest(models.Model):
    _inherit = "sign.oca.request"

    def write(self, vals):
        # Capture pre-state per record (was the request NOT already in a signed state?)
        was_unsigned = {}
        if "state" in vals and vals.get("state") in SIGNED_STATES:
            for req in self:
                was_unsigned[req.id] = req.state not in SIGNED_STATES

        result = super().write(vals)

        if vals.get("state") in SIGNED_STATES:
            for req in self:
                if was_unsigned.get(req.id):
                    try:
                        req._trigger_post_nda_automation()
                    except Exception as exc:
                        _logger.exception(
                            "Plan 10-04: post-NDA automation failed for sign.oca.request %s: %s",
                            req.id,
                            exc,
                        )
        return result

    def _trigger_post_nda_automation(self):
        """Find the employee linked to this NDA and run post-NDA actions."""
        self.ensure_one()

        # sign.oca.request has signer_ids (OCA) — fallback to signatory_ids
        signers = False
        for field_name in ("signer_ids", "signatory_ids", "request_item_ids"):
            if field_name in self._fields:
                signers = self[field_name]
                break

        partner_ids = []
        if signers:
            for s in signers:
                partner = getattr(s, "partner_id", False)
                if partner:
                    partner_ids.append(partner.id)

        if not partner_ids:
            _logger.warning(
                "Plan 10-04: sign.oca.request %s has no signer partners, skipping",
                self.id,
            )
            return

        partners = self.env["res.partner"].browse(partner_ids)
        HrEmployee = self.env["hr.employee"].sudo()
        HrCandidate = self.env["hr.candidate"].sudo()

        for partner in partners:
            employee = HrEmployee.search(
                [("work_contact_id", "=", partner.id)], limit=1
            )
            if not employee:
                employee = HrEmployee.search(
                    [
                        "|",
                        ("work_email", "=", partner.email),
                        ("private_email", "=", partner.email),
                    ],
                    limit=1,
                )
            if not employee:
                candidate = HrCandidate.search(
                    [("partner_name", "=", partner.name)], limit=1
                )
                if candidate:
                    employee = candidate.employee_id
            if not employee:
                _logger.info(
                    "Plan 10-04: sign.oca.request %s signer %s not linked to any employee, skipping",
                    self.id,
                    partner.id,
                )
                continue

            checklist = employee.onboarding_checklist_id
            if checklist:
                checklist.sudo().write(
                    {"nda_signed": True, "nda_sign_request_id": self.id}
                )

            # Plan 10-05 F3: fail-safe — skip enrollment + WA if portal creation
            # fails to avoid partial onboarding state.
            portal_user_ok = True
            if not employee.user_id:
                try:
                    employee.action_create_portal_user()
                except Exception as exc:
                    portal_user_ok = False
                    _logger.exception(
                        "Plan 10-05: portal user creation failed for employee %s: %s",
                        employee.id,
                        exc,
                    )

            if not portal_user_ok:
                _logger.warning(
                    "Plan 10-05: skipping enrollment+WA for employee %s because "
                    "portal creation failed (F3 fail-safe)",
                    employee.id,
                )
                continue

            try:
                employee.action_enroll_courses_by_job()
            except Exception as exc:
                _logger.exception(
                    "Plan 10-04: course enrollment failed for employee %s: %s",
                    employee.id,
                    exc,
                )

            # Send welcome WhatsApp
            try:
                self.env["hr.applicant"]._send_welcome_wa_message(employee)
            except Exception as exc:
                _logger.exception(
                    "Plan 10-04: welcome WA failed for employee %s: %s",
                    employee.id,
                    exc,
                )
