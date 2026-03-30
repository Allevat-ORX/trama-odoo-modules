# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "Recruitment Interview Booking",
    "summary": "Integrate resource_booking with hr.applicant for interview scheduling",
    "version": "18.0.1.0.0",
    "license": "AGPL-3",
    "author": "OnRentX",
    "depends": [
        "hr_recruitment",
        "resource_booking",
        "hr_applicant_whatsapp",
        "survey",
    ],
    "data": [
        "data/ir_config_parameter_data.xml",
        "data/booking_type_data.xml",
        "data/cron_survey_reminder.xml",
        "security/ir.model.access.csv",
        "wizard/wa_chatbot_start_wizard_view.xml",
        "views/hr_applicant_views.xml",
    ],
    "installable": True,
    "auto_install": False,
}
