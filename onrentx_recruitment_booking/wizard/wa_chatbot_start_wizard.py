# Copyright 2026 OnRentX
from odoo import fields, models, _
from odoo.exceptions import UserError


class WaChatbotStartWizard(models.TransientModel):
    _name = "wa.chatbot.start.wizard"
    _description = "Iniciar Chatbot WA - Elegir sender"

    applicant_id = fields.Many2one("hr.applicant", required=True)
    wasender_config_id = fields.Many2one(
        "onrentx.wasender.config",
        string="Enviar desde",
        required=True,
    )

    def action_start(self):
        """Start chatbot with selected sender."""
        self.ensure_one()
        self.applicant_id._start_wa_chatbot(self.wasender_config_id)
        return {"type": "ir.actions.act_window_close"}
