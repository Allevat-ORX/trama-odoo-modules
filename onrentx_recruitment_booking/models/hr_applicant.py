# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)

BOOKING_TYPE_NAME = "Entrevista de Reclutamiento OnRentX"
INTERVIEW_TEMPLATE_NAME = "Recruitment: Schedule Interview"


class HrApplicant(models.Model):
    _inherit = "hr.applicant"

    booking_id = fields.Many2one(
        "resource.booking",
        string="Entrevista agendada",
        copy=False,
        ondelete="set null",
    )
    booking_state = fields.Selection(
        related="booking_id.state",
        string="Estado cita",
    )
    booking_portal_url = fields.Char(
        string="URL agenda entrevista",
        compute="_compute_booking_portal_url",
    )
    booking_start = fields.Datetime(
        related="booking_id.start",
        string="Fecha entrevista",
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Send welcome WA when new applicant is created with phone."""
        records = super().create(vals_list)
        for rec in records:
            try:
                rec._send_welcome_wa()
            except Exception as e:
                _logger.error("Welcome WA failed for applicant %d: %s", rec.id, e)
        return records

    def _send_welcome_wa(self):
        """Send welcome WA if applicant has phone and hasn't been contacted."""
        self.ensure_one()
        phone = self.partner_phone
        if not phone:
            return

        # Only for idle applicants without prior WA contact
        if self.wa_chat_state != "idle":
            return
        data = self._get_wa_data()
        if data.get("welcome_sent"):
            return

        job_name = self.job_id.name if self.job_id else "una vacante"
        candidate_name = self.partner_name or "candidato/a"

        msg = (
            "Hola %s 👋\n\n"
            "Recibimos tu postulación para *%s* en *OnRentX*. "
            "¡Gracias por tu interés!\n\n"
            "Te enviamos un cuestionario a tu correo electrónico. "
            "Complétalo para avanzar en el proceso de selección. 📋\n\n"
            "Si tienes dudas, escríbenos por aquí. 😊"
        ) % (candidate_name, job_name)

        config = self._get_wasender_config()
        if config:
            sent = self._send_wa_with_config(phone, msg, config)
            if sent:
                data["welcome_sent"] = True
                data["wasender_config_id"] = config.id
                self._set_wa_data(data)
                _logger.info("Welcome WA sent to applicant %d (%s)", self.id, self.partner_name)

    @api.depends("booking_id", "booking_id.access_token")
    def _compute_booking_portal_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        for rec in self:
            if rec.booking_id:
                portal_path = rec.booking_id.get_portal_url()
                rec.booking_portal_url = f"{base_url}{portal_path}"
            else:
                rec.booking_portal_url = False

    def action_create_interview_booking(self):
        """Create booking AND open email composer with template 102 in one step.
        If booking already exists, skip creation and go straight to email composer.
        """
        self.ensure_one()

        if self.booking_id:
            # Booking exists → just open email composer to resend
            return self._open_interview_email_composer()

        if not self.email_from:
            raise UserError(_(
                "Este candidato no tiene correo electrónico. "
                "Agregue un email antes de crear la cita."
            ))

        partner = self._get_or_create_partner()

        # Get booking type
        booking_type = self.env["resource.booking.type"].search([
            ("name", "=", BOOKING_TYPE_NAME),
        ], limit=1)
        if not booking_type:
            booking_type = self.env["resource.booking.type"].search([
                ("name", "ilike", "Entrevista"),
            ], limit=1)
        if not booking_type:
            raise UserError(_(
                "No se encontró el tipo de reserva para entrevistas. "
                "Configure uno en Resource Bookings > Configuration > Booking Types."
            ))

        # Create booking
        booking = self.env["resource.booking"].create({
            "type_id": booking_type.id,
            "partner_ids": [(6, 0, [partner.id])],
            "combination_auto_assign": True,
        })
        self.booking_id = booking.id

        # Post in chatter
        portal_url = self.booking_portal_url
        body_html = (
            '<div style="background:#e8f5e9;padding:10px;border-left:4px solid #4CAF50;border-radius:6px;">'
            '<b>📅 Cita de entrevista creada</b><br/>'
            'Estado: <b>Pendiente</b> — esperando que el candidato elija horario.<br/>'
            '<a href="%s" target="_blank">🔗 Link para el candidato</a>'
            '</div>'
        ) % portal_url
        self.message_post(
            body=Markup(body_html),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        _logger.info(
            "Booking %d created for applicant %d (%s) - portal URL: %s",
            booking.id, self.id, self.partner_name, portal_url,
        )

        return self._open_interview_email_composer()

    def _open_interview_email_composer(self):
        """Open email composer with template 102 (interview scheduling)."""
        template = self.env["mail.template"].search([
            ("name", "=", INTERVIEW_TEMPLATE_NAME),
            ("model", "=", "hr.applicant"),
        ], limit=1)

        if template:
            return {
                "name": _("Enviar cita de entrevista a %s") % self.partner_name,
                "type": "ir.actions.act_window",
                "res_model": "mail.compose.message",
                "view_mode": "form",
                "target": "new",
                "context": {
                    "default_model": "hr.applicant",
                    "default_res_ids": [self.id],
                    "default_template_id": template.id,
                    "default_composition_mode": "comment",
                    "force_email": True,
                },
            }

        # Fallback: show notification with URL
        portal_url = self.booking_portal_url or ""
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Cita creada"),
                "message": _(
                    "Cita creada. Envíe este link al candidato: %s"
                ) % portal_url,
                "type": "success",
                "sticky": True,
            },
        }

    def action_confirm_jcf(self):
        """Confirm JCF verified → create booking if needed → send email + WA with link."""
        self.ensure_one()

        if self.wa_chat_state != 'pasa_pendiente_jcf':
            raise UserError(_(
                "Este candidato no está en estado 'Pendiente JCF'. Estado actual: %s"
            ) % dict(self._fields["wa_chat_state"].selection).get(
                self.wa_chat_state, self.wa_chat_state
            ))

        # 1. Create booking if not exists
        if not self.booking_id:
            partner = self._get_or_create_partner()
            booking_type = self.env["resource.booking.type"].search([
                ("name", "=", BOOKING_TYPE_NAME),
            ], limit=1)
            if not booking_type:
                booking_type = self.env["resource.booking.type"].search([
                    ("name", "ilike", "Entrevista"),
                ], limit=1)
            if not booking_type:
                raise UserError(_("No se encontró el tipo de reserva para entrevistas."))

            booking = self.env["resource.booking"].create({
                "type_id": booking_type.id,
                "partner_ids": [(6, 0, [partner.id])],
                "combination_auto_assign": True,
            })
            self.booking_id = booking.id
            _logger.info("Booking %d created for applicant %d", booking.id, self.id)

        # 2. Change state to listo_entrevista
        self.wa_chat_state = "listo_entrevista"
        # Move pipeline → Entrevista enviada (id=15)
        try:
            self.stage_id = 15
        except Exception:
            pass

        # 3. Post in chatter
        portal_url = self.booking_portal_url
        self.message_post(
            body=Markup(
                '<div style="background:#e8f5e9;padding:10px;border-left:4px solid #4CAF50;border-radius:8px;">'
                '<b>✅ JCF verificado — Listo para entrevista</b><br/>'
                'Booking creado. Link enviado al candidato por email y WhatsApp.<br/>'
                '<a href="%s" target="_blank">🔗 Link de agenda</a>'
                '</div>'
            ) % portal_url,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # 4. Send WA with booking link
        wa_data = self._get_wa_data()
        config_id = wa_data.get("wasender_config_id")
        config = None
        if config_id:
            config = self.env["onrentx.wasender.config"].browse(config_id)
        if not config or not config.exists() or not config.api_key:
            config = self._get_wasender_config()

        if config and self.partner_phone:
            wa_msg = (
                "¡Excelente %s! ✅ Tu comprobante JCF fue verificado.\n\n"
                "Ahora puedes agendar tu entrevista presencial:\n\n"
                "👉 %s\n\n"
                "Elige el horario que mejor te funcione. ¡Te esperamos! 📅"
            ) % (self.partner_name or "", portal_url)
            self._send_wa_with_config(self.partner_phone, wa_msg, config)

        # 5. Open email composer with template 102
        return self._open_interview_email_composer()

    def action_cancel_interview_booking(self):
        """Cancel the current booking."""
        self.ensure_one()
        if not self.booking_id:
            raise UserError(_("Este candidato no tiene cita de entrevista."))
        self.booking_id.action_cancel()
        # booking_id gets cleared by the resource_booking write hook

    def action_view_booking(self):
        """Open the booking form."""
        self.ensure_one()
        if not self.booking_id:
            raise UserError(_("Este candidato no tiene cita de entrevista."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "resource.booking",
            "res_id": self.booking_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def _send_reschedule_email(self):
        """Send reschedule email when event is deleted from Google Calendar."""
        self.ensure_one()
        template = self.env.ref(
            "onrentx_recruitment_booking.mail_template_interview_reschedule",
            raise_if_not_found=False,
        )
        if template and self.email_from:
            template.send_mail(self.id, force_send=True)
            _logger.info(
                "Reschedule email sent to applicant %d (%s) at %s",
                self.id, self.partner_name, self.email_from,
            )
        else:
            _logger.warning(
                "Cannot send reschedule email: template=%s email=%s",
                bool(template), self.email_from,
            )

    def action_copy_booking_url(self):
        """Return the portal URL for easy copy."""
        self.ensure_one()
        if not self.booking_portal_url:
            raise UserError(_("Primero cree una cita de entrevista."))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("URL de agenda"),
                "message": self.booking_portal_url,
                "type": "info",
                "sticky": True,
            },
        }

    @api.model
    def _cron_survey_reminder(self):
        """Cron: remind candidates who haven't completed survey after 24h."""
        from datetime import timedelta
        cutoff = fields.Datetime.now() - timedelta(hours=24)

        # Find applicants: welcome sent, idle, created > 24h ago, no completed survey
        applicants = self.search([
            ("wa_chat_state", "=", "idle"),
            ("partner_phone", "!=", False),
            ("create_date", "<", cutoff),
            ("create_date", ">", fields.Datetime.now() - timedelta(days=5)),  # max 5 days old
        ])

        for app in applicants:
            try:
                data = app._get_wa_data()
                if not data.get("welcome_sent"):
                    continue
                if data.get("reminder_sent"):
                    continue

                # Check if survey is completed
                has_done = any(r.state == "done" for r in app.response_ids)
                if has_done:
                    continue

                job_name = app.job_id.name if app.job_id else "la vacante"
                msg = (
                    "Hola %s 👋\n\n"
                    "Te recordamos que tienes pendiente completar el "
                    "cuestionario para *%s* en OnRentX.\n\n"
                    "Revisa tu correo electrónico y complétalo para "
                    "avanzar en el proceso. ¡El tiempo es limitado! ⏰\n\n"
                    "Si necesitas que te lo reenviemos, dinos por aquí."
                ) % (app.partner_name or "candidato/a", job_name)

                config = app._get_wasender_config()
                if config:
                    sent = app._send_wa_with_config(app.partner_phone, msg, config)
                    if sent:
                        data["reminder_sent"] = True
                        app._set_wa_data(data)
                        _logger.info("Survey reminder sent to applicant %d (%s)", app.id, app.partner_name)
            except Exception as e:
                _logger.error("Survey reminder failed for applicant %d: %s", app.id, e)
