# Copyright 2026 OnRentX
import re
import logging
import requests
from odoo import fields, models, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class WaMassSendWizard(models.TransientModel):
    _name = "wa.mass.send.wizard"
    _description = "Envio masivo WhatsApp a candidatos"

    applicant_ids = fields.Many2many("hr.applicant", string="Candidatos")
    wasender_config_id = fields.Many2one(
        "onrentx.wasender.config",
        string="Enviar desde",
        required=True,
    )
    template_id = fields.Many2one(
        "onrentx.whatsapp.template",
        string="Plantilla",
        help="Selecciona una plantilla o deja vacio para escribir mensaje libre.",
    )
    message = fields.Text(
        string="Mensaje",
        help="Usa {nombre} para el nombre del candidato, {puesto} para el puesto, {link_portal} para el link de cita.",
    )
    applicant_count = fields.Integer(
        string="Candidatos seleccionados",
        compute="_compute_applicant_count",
    )

    @api.depends('applicant_ids')
    def _compute_applicant_count(self):
        for rec in self:
            rec.applicant_count = len(rec.applicant_ids)

    @api.onchange('template_id')
    def _onchange_template_id(self):
        if self.template_id and self.template_id.body:
            self.message = self.template_id.body

    def action_send(self):
        """Send WhatsApp message to all selected applicants."""
        self.ensure_one()
        if not self.applicant_ids:
            raise UserError(_("No hay candidatos seleccionados."))
        if not self.message:
            raise UserError(_("Escribe un mensaje o selecciona una plantilla."))

        config = self.wasender_config_id
        if not config.api_key:
            raise UserError(_("El sender seleccionado no tiene API key configurada."))

        errors = []
        sent = 0
        for app in self.applicant_ids:
            phone = app.partner_phone or ''
            phone = re.sub(r'[^\d+]', '', phone)
            if not phone:
                errors.append(_("%s: sin telefono") % (app.partner_name or app.display_name))
                continue
            if not phone.startswith('+'):
                if not phone.startswith('52'):
                    phone = '52' + phone
                phone = '+' + phone

            # Replace placeholders
            msg = self.message
            msg = msg.replace('{nombre}', app.partner_name or '')
            msg = msg.replace('{puesto}', app.job_id.name if app.job_id else '')
            if '{link_portal}' in msg:
                msg = msg.replace('{link_portal}', app.booking_portal_url or '(sin link)')

            try:
                resp = requests.post(
                    "https://wasenderapi.com/api/send-message",
                    json={"to": phone, "text": msg},
                    headers={
                        "Authorization": "Bearer %s" % config.api_key,
                        "Content-Type": "application/json",
                    },
                    timeout=15,
                )
                if 200 <= resp.status_code < 300:
                    sent += 1
                    app.message_post(
                        body=_("WA masivo enviado: %s") % msg[:200],
                        message_type='comment',
                        subtype_xmlid='mail.mt_note',
                    )
                else:
                    errors.append(
                        _("%s: HTTP %s") % (app.partner_name or app.display_name, resp.status_code)
                    )
            except Exception as e:
                errors.append(
                    _("%s: %s") % (app.partner_name or app.display_name, str(e))
                )

        msg_result = _("Enviados: %d de %d") % (sent, len(self.applicant_ids))
        if errors:
            msg_result += "\n\n" + _("Errores:") + "\n" + "\n".join(errors)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Envio WhatsApp"),
                "message": msg_result,
                "type": "success" if not errors else "warning",
                "sticky": True,
            },
        }
