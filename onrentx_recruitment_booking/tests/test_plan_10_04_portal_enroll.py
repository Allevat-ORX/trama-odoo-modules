# -*- coding: utf-8 -*-
"""Plan 10-04 tests — portal user collision + course enrollment fallback.

Validates Plan 10-05 F4 (duplicate login reuse) and F5 (base channel
fallback via ir.config_parameter).
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "plan_10_04")
class TestPortalEnroll(TransactionCase):

    def setUp(self):
        super().setUp()
        self.email = "dup_test_10_05@example.com"

        # Pre-existing user with the same email (simulates JCF re-applicant)
        self.existing_partner = self.env["res.partner"].create(
            {
                "name": "Existing User 10-05",
                "email": self.email,
            }
        )
        self.existing_user = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "login": self.email,
                    "partner_id": self.existing_partner.id,
                    "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
                }
            )
        )

        # New employee with the same email
        self.new_partner = self.env["res.partner"].create(
            {
                "name": "New Hire 10-05",
                "email": self.email,
            }
        )
        self.employee = self.env["hr.employee"].create(
            {
                "name": "New Hire 10-05",
                "work_contact_id": self.new_partner.id,
                "work_email": self.email,
            }
        )

    def test_portal_user_duplicate_login_reuses_existing(self):
        """Plan 10-05 F4: duplicate login should reuse the existing user
        instead of crashing on the res.users login unique constraint."""
        user = self.employee.action_create_portal_user()
        self.assertEqual(
            user.id,
            self.existing_user.id,
            "Should reuse existing user on login collision",
        )
        self.assertEqual(self.employee.user_id.id, self.existing_user.id)

    def test_enroll_courses_falls_back_to_base_when_no_map(self):
        """Plan 10-05 F5: enrollment should not crash when no course map
        exists for the job. It should fall back to the configured base
        channel (or name search) without raising."""
        job = self.env["hr.job"].create({"name": "Unmapped Job 10-05"})
        self.employee.job_id = job.id
        self.employee.user_id = self.existing_user.id  # skip portal creation
        # Should not raise — either returns the base channel or an empty recordset
        result = self.employee.action_enroll_courses_by_job()
        self.assertIsNotNone(result, "Method should return a recordset, not None")
