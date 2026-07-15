# Changelog — onrentx_recruitment_booking

Módulo de reclutamiento OnRentX (chatbot WhatsApp, pipeline JCF, onboarding).
Producción: VM .80 (`/opt/odoo/custom-addons/ModulosOdoo/onrentx_recruitment_booking/`).

---

## [sync-vm80] 2026-07-15 — Reconciliación código vivo VM .80 → repo

**Contexto:** el código de producción en la VM .80 se editó directamente en el
servidor durante meses (22 backups `.bak`) y nunca se subió a este repo. El repo
`main` estaba ~633 líneas por detrás en `wa_chatbot.py` y le faltaban ~20 archivos
(subsistema JCF completo + onboarding). Este commit captura el estado vivo real de
producción como baseline versionado. Ref: Jira RH-60, RH-49, RH-50.

**Diff vs main previo:** 54 archivos, +7.480 / -1.491.

### Añadido (estaba solo en la VM, ahora versionado)
- **JCF:** `models/jcf_vinculacion.py`, `data/jcf_vinculacion_cron.xml`,
  `views/jcf_vinculacion_views.xml`, `wizard/jcf_vinculacion_wizard.py`,
  `wizard/wa_mass_send_wizard.py`, `scripts/jcf_municipios_monitor.py`.
- **Onboarding:** `models/hr_applicant_onboarding.py`, `hr_applicant_hired_automation.py`,
  `hr_applicant_welcome.py`, `hr_employee_onboarding.py`, `onboarding_checklist.py`,
  `onboarding_course_map.py`, `hr_job_course_map.py`, `mail_templates_onboarding.xml`.
- **Ranking / dashboard:** `candidate_rankings_wizard.py`,
  `reports/recruitment_ranking_report.xml`, `views/recruitment_dashboard_views.xml`.
- **Crons/templates:** `cron_chatbot_lifecycle.xml`, `cron_metrics_update.xml`,
  `email_templates.xml`, `wa_templates.xml`, `hired_stage_config.xml`.
- **OCA Sign / eLearning:** `sign_oca_request_extension.py`,
  `slide_channel_partner_completion.py` + tests (`test_plan_10_04_*`).

### Cambiado
- `wa_chatbot.py`: 1.269 → 1.902 líneas (máquina de 14 estados WA completa,
  screening LLM, loop detection).
- `__manifest__.py`, `hr_applicant.py`, `survey_auto_trigger.py`, security ACL.

### Notas
- Excluidos del commit: `*.bak*` (22 backups), `__pycache__`, `.pyc`.
- Sin credenciales hardcodeadas (scan verificado). Token WaSender vive en
  `wasender.config` (DB), referenciado por `config_id=4` (magic number, RH-48).
- **Pendiente:** la VM sigue siendo copia suelta (no checkout git). Convertir la VM
  en checkout o montar deploy git→VM (RH-50/RH-51).

---

## [pre-sync] histórico
Versiones anteriores editadas directamente en VM .80 sin registro en git
(2026-04 a 2026-07). Ver backups `.bak.*` en el servidor si se necesita arqueología.
