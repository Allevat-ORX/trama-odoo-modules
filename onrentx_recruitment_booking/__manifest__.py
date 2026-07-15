# -*- coding: utf-8 -*-
{
    'name': 'OnRentX Recruitment Booking',
    'version': '1.3.1',
    'category': 'Human Resources/Recruitment',
    'summary': 'Recruitment pipeline with AI evaluation, scheduling, and metrics',
    'description': """
        OnRentX Recruitment Module
        ==========================
        * AI-powered candidate evaluation via WhatsApp
        * Automated survey scoring
        * Calendar booking integration
        * Metrics dashboard and reporting
        * Funnel visualization
        * Mass WhatsApp sending with templates
        * Phase 10: Auto-trigger pipeline on applicant creation
        * Phase 10: 14 WA chat states (added videoentrevista, registro_jcf, onboarding, contrato_firmado)
        * Phase 10: Opt-out detection and cron lifecycle fixes
        * Plan 10-04: Post-hired onboarding automation (checklist, portal, courses)
        * Plan 10-05: Gap closure — F1..F8 fixes + post_init_hook course seeds
    """,
    'author': 'OnRentX',
    'website': 'https://onrentx.com',
    'depends': [
        'base',
        'hr_recruitment',
        'hr',
        'calendar',
        'survey',
        'website_slides',
        'sign_oca',
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_config_parameter_data.xml',
        'data/ir_config_parameter_10_05.xml',
        'data/booking_type_data.xml',
        'data/cron_metrics_update.xml',
        'data/cron_survey_reminder.xml',
        'data/cron_chatbot_lifecycle.xml',
        'data/jcf_vinculacion_cron.xml',
        'data/email_templates.xml',
        'data/wa_templates.xml',
        'data/hired_stage_config.xml',
        'data/mail_templates_onboarding.xml',
        'views/recruitment_dashboard_views.xml',
        'views/hr_applicant_views.xml',
        'views/jcf_vinculacion_views.xml',
        'views/hr_job_views.xml',
        'views/onboarding_checklist_views.xml',
        'views/hr_employee_views_10_04.xml',
        'data/course_map_data.xml',
        'wizard/wa_chatbot_start_wizard_view.xml',
        'wizard/wa_mass_send_wizard_view.xml',
        'reports/recruitment_ranking_report.xml',
    ],
    'demo': [],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
    # Plan 10-05 F7 fix: idempotent course map seed runs on install + upgrade
    'post_init_hook': 'post_init_seed_course_maps',
}
