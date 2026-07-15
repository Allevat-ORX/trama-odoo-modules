# Copyright 2026 OnRentX
import logging
from odoo import fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class WaChatbotStartWizard(models.TransientModel):
    _name = "wa.chatbot.start.wizard"
    _description = "Iniciar Chatbot WA - Elegir sender"

    # Backwards-compatible: keeps applicant_id for single-use from form button
    applicant_id = fields.Many2one("hr.applicant")
    # New: supports multi-selection from list view
    applicant_ids = fields.Many2many("hr.applicant", string="Candidatos")
    wasender_config_id = fields.Many2one(
        "onrentx.wasender.config",
        string="Enviar desde",
        required=True,
    )

    def action_start(self):
        """Start chatbot for one or multiple applicants."""
        self.ensure_one()
        # Merge single + multi into one recordset
        applicants = self.applicant_ids | self.applicant_id
        if not applicants:
            raise UserError(_("No hay candidatos seleccionados."))

        errors = []
        sent = 0
        for app in applicants:
            try:
                if app.wa_chat_state not in ("idle", "rechazado"):
                    errors.append(
                        _("%s: ya en estado '%s', se omite.")
                        % (app.partner_name or app.display_name,
                           dict(app._fields['wa_chat_state'].selection).get(app.wa_chat_state, app.wa_chat_state))
                    )
                    continue
                app._start_wa_chatbot(self.wasender_config_id)
                sent += 1
            except Exception as e:
                errors.append(
                    _("%s: %s") % (app.partner_name or app.display_name, str(e))
                )

        # Show summary if multi-send
        if len(applicants) > 1:
            msg = _("Enviados: %d de %d") % (sent, len(applicants))
            if errors:
                msg += "\n\n" + _("Errores:") + "\n" + "\n".join(errors)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Envio masivo WhatsApp"),
                    "message": msg,
                    "type": "success" if not errors else "warning",
                    "sticky": bool(errors),
                },
            }
        return {"type": "ir.actions.act_window_close"}
