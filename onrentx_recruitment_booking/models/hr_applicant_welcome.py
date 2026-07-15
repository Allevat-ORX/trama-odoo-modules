# -*- coding: utf-8 -*-
"""Welcome WA helper + doc collection extension for plan 10-04 / fix pass 10-05.

Adds _send_welcome_wa_message() and extends doc collection to 5 docs
(adds RFC and carta_jcf to the 3 existing docs from plan 10-03).

Plan 10-05 F6 fix: use web.base.url ir.config_parameter instead of hardcoded
LAN URL so WhatsApp links work from mobile networks outside the office.
"""
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

# Plan 10-04: extended from 4 docs (plan 10-03) to 6 items (5 logical docs).
# INE is split into frente + vuelta photos.
ONBOARDING_DOC_ORDER = [
    (
        "comprobante_domicilio",
        "Envia una foto de tu comprobante de domicilio reciente (max 3 meses).",
    ),
    ("curp", "Ahora envia foto de tu CURP."),
    ("ine_frente", "Envia foto de tu INE por el frente."),
    ("ine_vuelta", "Ahora el reverso de tu INE."),
    ("rfc", "Envia foto o PDF de tu constancia de situacion fiscal (RFC)."),
    ("carta_jcf", "Por ultimo, envia foto de tu carta de vinculacion JCF."),
]


class HrApplicantWelcome(models.Model):
    _inherit = "hr.applicant"

    @api.model
    def _send_welcome_wa_message(self, employee):
        """Send welcome WhatsApp with portal + courses link to an employee.

        Called from sign_oca_request_extension after NDA signed.
        """
        if not employee:
            return False

        partner = employee.work_contact_id
        phone = (
            employee.work_phone
            or employee.mobile_phone
            or (partner.phone if partner else False)
            or (partner.mobile if partner else False)
        )
        if not phone:
            _logger.warning(
                "Plan 10-04: no phone for employee %s, skipping welcome WA", employee.id
            )
            return False

        # Plan 10-05 F6 fix: resolve courses URL via ir.config_parameter so
        # candidates can open the link from mobile networks (not just LAN).
        ICP = self.env["ir.config_parameter"].sudo()
        base_url = ICP.get_param("web.base.url", "https://odoo.tramarental.com")
        path = ICP.get_param("onrentx.onboarding.welcome_url_path", "/slides")
        courses_url = (base_url or "").rstrip("/") + path

        msg = (
            "Bienvenido/a al equipo OnRentX, %s! 🎉\n\n"
            "Tu acceso al portal y cursos ya esta listo.\n"
            "📧 Recibiras un correo con tu contrasena inicial (revisa spam).\n"
            "🎓 Tus cursos de onboarding estan disponibles en: %s\n\n"
            "Si necesitas ayuda, escribenos por aqui mismo."
        ) % (employee.name, courses_url)

        # Use existing WaSender integration via ir.config_parameter or wasender service
        try:
            WaSender = self.env["wasender.service"]
            WaSender._send_message(phone, msg, config_id=4)
            _logger.info(
                "Plan 10-04: welcome WA sent to %s (%s)", employee.name, phone
            )
            return True
        except Exception as exc:
            _logger.exception(
                "Plan 10-04: wasender.service failed (%s), trying direct HTTP",
                exc,
            )

        # Fallback: direct HTTP call to wasender
        try:
            import requests

            api_key = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("wasender.api_key")
            )
            if not api_key:
                _logger.error("Plan 10-04: no wasender.api_key in ir.config_parameter")
                return False

            response = requests.post(
                "https://wasenderapi.com/api/send-message",
                headers={
                    "Authorization": "Bearer %s" % api_key,
                    "Content-Type": "application/json",
                },
                json={"to": phone, "text": msg},
                timeout=15,
            )
            if response.status_code == 200:
                _logger.info(
                    "Plan 10-04: welcome WA sent via HTTP fallback to %s", phone
                )
                return True
            _logger.error(
                "Plan 10-04: wasender HTTP %s: %s", response.status_code, response.text
            )
        except Exception as exc:
            _logger.exception("Plan 10-04: welcome WA HTTP fallback failed: %s", exc)

        return False
