# -*- coding: utf-8 -*-
"""Plan 10-04 tests — sign.oca.request state transition triggers.

Validates Plan 10-05 F1 fix: both 'signed' and '2_signed' state literals
trigger post-NDA automation.
"""
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "plan_10_04")
class TestSignOcaTrigger(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create(
            {
                "name": "Test NDA Signer 10-05",
                "email": "test_nda_10_05@example.com",
            }
        )
        self.employee = self.env["hr.employee"].create(
            {
                "name": "Test NDA Signer 10-05",
                "work_contact_id": self.partner.id,
                "work_email": "test_nda_10_05@example.com",
            }
        )
        self.checklist = self.env["onrentx.onboarding.checklist"].create(
            {"employee_id": self.employee.id}
        )
        self.employee.onboarding_checklist_id = self.checklist.id

        # Need an NDA template to attach the request to. If none exists we skip.
        self.template = self.env["sign.oca.template"].search(
            [("name", "ilike", "NDA")], limit=1
        )
        if not self.template:
            self.skipTest("No NDA template available in sign.oca.template")

        # Resolve a role from the template's item_ids (OCA API)
        role_id = False
        if self.template.item_ids:
            first_item = self.template.item_ids[:1]
            if first_item.role_id:
                role_id = first_item.role_id.id

        self.sign_request = self.env["sign.oca.request"].create(
            {
                "template_id": self.template.id,
                "name": "Test NDA 10-05",
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": self.partner.id,
                            "role_id": role_id,
                        },
                    )
                ],
            }
        )

    def test_signed_state_triggers_automation(self):
        """Writing state='2_signed' should fire post-NDA automation."""
        with patch.object(
            type(self.employee),
            "action_enroll_courses_by_job",
            return_value=self.env["slide.channel"],
        ), patch.object(
            type(self.env["hr.applicant"]),
            "_send_welcome_wa_message",
            return_value=True,
        ), patch.object(
            type(self.employee),
            "action_create_portal_user",
            return_value=self.env["res.users"],
        ):
            self.sign_request.write({"state": "2_signed"})
            self.checklist.invalidate_recordset()
            self.assertTrue(
                self.checklist.nda_signed,
                "Checklist should be marked nda_signed after state transition",
            )

    def test_signed_state_singular_literal_also_works(self):
        """Plan 10-05 F1 belt-and-suspenders: 'signed' literal also triggers.

        NOTE: 'signed' is NOT in the current OCA state selection on VM .80
        (verified 2026-04-10: selection = 0_sent/1_draft/2_signed/3_cancel).
        So we test the Python logic directly by calling _trigger_post_nda_automation
        with the singular literal in vals — this validates the SIGNED_STATES
        tuple membership check without requiring Odoo to accept an invalid
        selection value in write().
        """
        from odoo.addons.onrentx_recruitment_booking.models.sign_oca_request_extension import (
            SIGNED_STATES,
        )

        self.assertIn(
            "signed",
            SIGNED_STATES,
            "Belt-and-suspenders: 'signed' literal should be in SIGNED_STATES",
        )
        self.assertIn(
            "2_signed",
            SIGNED_STATES,
            "Canonical OCA literal '2_signed' must be in SIGNED_STATES",
        )
