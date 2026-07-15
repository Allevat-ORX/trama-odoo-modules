# -*- coding: utf-8 -*-
# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
Onboarding document collection + NDA signing for hr.applicant.

After a candidate is vinculado in JCF (state: registro_jcf -> onboarding):
  1. Bot requests documents one-by-one via WhatsApp:
     - Comprobante de domicilio (photo)
     - CURP (photo)
     - INE frente (photo)
     - INE vuelta (photo)
  2. Each photo is attached to hr.applicant as ir.attachment
  3. After all docs collected, bot sends Odoo Sign NDA link
  4. NDA completion (detected via cron) moves to contrato_firmado state

Integration:
  - Called from wa_chatbot.py handle_wa_incoming when state='onboarding'
  - Uses WaSender media URL for image download
  - Uses Odoo Sign (sign.oca.template, sign.oca.request) for NDA
  - Graceful degradation: NDA flow skipped if template not found

Usage:
  applicant._request_next_document()    # Ask for next pending doc
  applicant._attach_onboarding_document(doc_type, media_url)  # Save image + ask next
  applicant._send_nda_sign_request()    # Send NDA link after all docs
  applicant._check_nda_signed()         # Called by cron to detect completion
"""

import base64
import logging

import requests
from markupsafe import Markup

from odoo import api, models

_logger = logging.getLogger(__name__)

# Order of documents to collect (key, human label, request message)
ONBOARDING_DOCS = [
    (
        "comprobante_domicilio",
        "Comprobante de domicilio",
        "Envia una foto de tu *comprobante de domicilio* reciente (max 3 meses). "
        "Puede ser recibo de luz, agua, gas o telefono.",
    ),
    (
        "curp",
        "CURP",
        "Ahora envia una foto de tu *CURP*. "
        "Puedes imprimirla desde https://www.gob.mx/curp",
    ),
    (
        "ine_frente",
        "INE (frente)",
        "Envia una foto del *frente de tu INE* (credencial para votar).",
    ),
    (
        "ine_vuelta",
        "INE (reverso)",
        "Ahora envia una foto del *reverso de tu INE*.",
    ),
    (
        "rfc",
        "RFC (constancia fiscal)",
        "Envia una foto o PDF de tu *constancia de situacion fiscal (RFC)*. "
        "Puedes descargarla desde https://www.sat.gob.mx",
    ),
    (
        "carta_jcf",
        "Carta Vinculacion JCF",
        "Por ultimo, envia una foto de tu *carta de vinculacion JCF*.",
    ),
]


class HrApplicantOnboarding(models.Model):
    _inherit = "hr.applicant"

    # ─── Public API ───

    def _request_next_document(self):
        """
        Check which onboarding docs are already collected and request the next missing one.
        If all docs are collected, trigger NDA sign request.
        Called at the start of onboarding state and after each doc received.
        """
        self.ensure_one()
        data = self._get_wa_data()
        collected = data.get("onboarding_docs", {})

        for doc_key, doc_label, doc_msg in ONBOARDING_DOCS:
            if not collected.get(doc_key):
                _logger.info(
                    "Requesting onboarding doc '%s' from applicant %d (%s)",
                    doc_key, self.id, self.partner_name,
                )
                self._send_wa(self.partner_phone, doc_msg)
                return

        # All documents collected — send NDA
        _logger.info(
            "All onboarding docs collected for applicant %d (%s), sending NDA",
            self.id, self.partner_name,
        )
        self._send_nda_sign_request()

    def _attach_onboarding_document(self, doc_type, media_url):
        """
        Download image from WaSender media URL, save as ir.attachment on hr.applicant,
        mark doc as collected in wa_chat_data, then request the next document.

        Args:
            doc_type: One of the doc_key values in ONBOARDING_DOCS
                      (comprobante_domicilio, curp, ine_frente, ine_vuelta).
                      Can also be 'auto' to auto-detect from missing docs.
            media_url: WaSender media URL to download the image from.
        """
        self.ensure_one()
        data = self._get_wa_data()
        collected = data.setdefault("onboarding_docs", {})

        # Auto-detect doc_type if not specified
        if doc_type == "auto":
            doc_type = self._get_next_missing_doc(collected)
            if not doc_type:
                # All docs already collected — redirect to NDA
                self._send_nda_sign_request()
                return

        doc_label = next(
            (label for key, label, _ in ONBOARDING_DOCS if key == doc_type),
            doc_type,
        )

        # Download image from WaSender media URL
        image_data = None
        try:
            resp = requests.get(media_url, timeout=30)
            resp.raise_for_status()
            image_data = resp.content
        except Exception as exc:
            _logger.warning(
                "Failed to download onboarding doc '%s' from %s for applicant %d: %s",
                doc_type, media_url, self.id, exc,
            )
            # Ask candidate to resend
            self._send_wa(
                self.partner_phone,
                "No pude descargar la imagen. Por favor reenvía la foto de tu *%s* 📷" % doc_label,
            )
            return

        # Create ir.attachment
        try:
            attachment = self.env["ir.attachment"].sudo().create({
                "name": "Onboarding - %s - %s" % (doc_label, self.partner_name or "candidato"),
                "res_model": "hr.applicant",
                "res_id": self.id,
                "datas": base64.b64encode(image_data),
                "mimetype": "image/jpeg",
                "type": "binary",
            })
            _logger.info(
                "Attached onboarding doc '%s' (attachment %d) to applicant %d",
                doc_type, attachment.id, self.id,
            )
        except Exception as exc:
            _logger.error(
                "Failed to create ir.attachment for onboarding doc '%s', applicant %d: %s",
                doc_type, self.id, exc,
            )
            self._send_wa(
                self.partner_phone,
                "Tuve un error guardando tu documento. Por favor reenvía la foto de tu *%s* 📷" % doc_label,
            )
            return

        # Mark doc as collected
        collected[doc_type] = True
        self._set_wa_data(data)

        # Post to chatter
        self.with_user(1).message_post(
            body=Markup(
                '<div style="background:#e8f5e9;padding:8px;border-left:4px solid #4CAF50;border-radius:6px;">'
                '<b>📎 Documento de onboarding recibido:</b> %s (adjunto #%d)</div>'
            ) % (doc_label, attachment.id),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # Request next document (or NDA if all collected)
        self._request_next_document()

    def _send_nda_sign_request(self):
        """
        Create an Odoo Sign NDA request for this applicant and send the signing URL via WA.

        Graceful degradation: if NDA template not found, logs warning and notifies Aleix
        without crashing or blocking the onboarding flow. Sends a fallback WA message.
        """
        self.ensure_one()

        # Search for NDA template in Odoo Sign
        sign_template = None
        try:
            sign_template = self.env["sign.oca.template"].sudo().search(
                [("name", "ilike", "NDA")], limit=1
            )
        except Exception as exc:
            _logger.warning(
                "Odoo Sign not available or error searching NDA template: %s", exc
            )

        if not sign_template:
            _logger.warning(
                "NDA sign template not found for applicant %d (%s). "
                "Notifying Aleix and continuing without blocking.",
                self.id, self.partner_name,
            )
            self._notify_aleix(
                "⚠️ *%s* completó todos los documentos de onboarding para *%s* "
                "pero NO hay plantilla NDA en Odoo Sign.\n"
                "Crea la plantilla en: Odoo Sign > Plantillas > Nueva\n"
                "Nombre: 'NDA Empleado'" % (
                    self.partner_name or "Candidato",
                    self.job_id.name if self.job_id else "?",
                )
            )
            # Move to contrato_firmado anyway so the process doesn't block
            self._send_wa(
                self.partner_phone,
                "Todos tus documentos fueron recibidos correctamente ✅\n\n"
                "El siguiente paso es la firma del acuerdo de confidencialidad. "
                "En breve te enviaremos el enlace para firmar. ¡Casi terminamos! 📝",
            )
            # Stay in onboarding state — Aleix will handle manually
            return

        # Get or create partner for this applicant
        partner = self._get_or_create_partner()

        # Create sign.oca.request
        # Plan 10-05 F8 fix: OCA sign uses sign.oca.template.item_ids[].role_id
        # (NOT the Enterprise sign_item_ids[].responsible_id API). Verified on
        # VM .80 2026-04-10: sign.oca.template fields = [..., 'item_ids', ...]
        # and each item_ids record has 'role_id' pointing to sign.oca.role.
        sign_request = None
        sign_url = None
        try:
            # Resolve the signer role from the template's item_ids
            first_item = sign_template.item_ids[:1] if sign_template.item_ids else None
            template_role_id = first_item.role_id.id if first_item and first_item.role_id else False
            if not template_role_id:
                _logger.warning(
                    "Plan 10-05 F8: NDA template %s has no item_ids with a role_id — "
                    "sign request will be created without a role (may fail validation)",
                    sign_template.id,
                )

            sign_request = self.env["sign.oca.request"].sudo().create({
                "template_id": sign_template.id,
                "name": "NDA - %s - %s" % (
                    self.partner_name or "Candidato",
                    self.job_id.name if self.job_id else "OnRentX",
                ),
                "signer_ids": [(0, 0, {
                    "partner_id": partner.id,
                    "role_id": template_role_id,
                })],
            })
            # Send the sign request (changes state to sent)
            sign_request.sudo().action_send()
            # Extract signing URL
            if sign_request.signer_ids:
                sign_url = sign_request.signer_ids[0].access_url
                # Make absolute URL
                base_url = self.env["ir.config_parameter"].sudo().get_param(
                    "web.base.url", "https://odoo.tramarental.com"
                )
                if sign_url and not sign_url.startswith("http"):
                    sign_url = base_url.rstrip("/") + sign_url
        except Exception as exc:
            _logger.error(
                "Failed to create sign.oca.request for applicant %d: %s", self.id, exc
            )

        # Store sign request reference in chat data
        data = self._get_wa_data()
        data["nda_sign_request_id"] = sign_request.id if sign_request else None
        data["nda_sent"] = True
        self._set_wa_data(data)

        # Send WA with NDA link
        if sign_url:
            self._send_wa(
                self.partner_phone,
                "¡Tus documentos fueron recibidos! ✅\n\n"
                "Ultimo paso: firma el *acuerdo de confidencialidad*. Es rapido, "
                "puedes firmarlo directamente desde tu celular:\n\n"
                "📝 %s\n\n"
                "Una vez firmado, estaremos en contacto con los detalles de tu incorporacion. "
                "¡Bienvenido/a al equipo! 🎉" % sign_url,
            )
        else:
            # sign.oca.request created but no URL (unusual — notify Aleix)
            self._send_wa(
                self.partner_phone,
                "¡Tus documentos fueron recibidos! ✅\n\n"
                "El ultimo paso es la firma del acuerdo de confidencialidad. "
                "En breve te contactaremos con el enlace para firmarlo. 📝",
            )
            self._notify_aleix(
                "⚠️ Sign.request creado para *%s* pero no se pudo extraer URL de firma. "
                "ID: %s" % (
                    self.partner_name or "Candidato",
                    sign_request.id if sign_request else "N/A",
                )
            )

        # Post to chatter
        self.with_user(1).message_post(
            body=Markup(
                '<div style="background:#e3f2fd;padding:8px;border-left:4px solid #2196F3;border-radius:6px;">'
                '<b>📝 NDA enviado</b> a %s<br/>Sign request: %s<br/>URL: %s</div>'
            ) % (
                self.partner_name or "Candidato",
                sign_request.id if sign_request else "N/A",
                sign_url or "No disponible",
            ),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        _logger.info(
            "NDA sign request sent to applicant %d (%s), sign_request_id=%s",
            self.id, self.partner_name,
            sign_request.id if sign_request else None,
        )

    def _check_nda_signed(self):
        """
        Check if the NDA sign.oca.request for this applicant has been completed.
        If signed: advance to contrato_firmado state and send WA confirmation.
        Called periodically by cron for candidates in 'onboarding' state.
        """
        self.ensure_one()
        data = self._get_wa_data()

        sign_request_id = data.get("nda_sign_request_id")
        if not sign_request_id:
            # No NDA was sent — check if all docs are still pending
            _logger.debug(
                "No NDA sign request recorded for applicant %d (%s), skipping NDA check",
                self.id, self.partner_name,
            )
            return False

        # Check sign.oca.request state
        try:
            sign_request = self.env["sign.oca.request"].sudo().browse(sign_request_id)
            if not sign_request.exists():
                _logger.warning(
                    "Sign request %d not found for applicant %d", sign_request_id, self.id
                )
                return False

            # Plan 10-05 F1: accept both 'signed' and '2_signed' (belt-and-suspenders)
            is_signed = sign_request.state in ("signed", "2_signed")
        except Exception as exc:
            _logger.warning(
                "Error checking sign.oca.request %d for applicant %d: %s",
                sign_request_id, self.id, exc,
            )
            return False

        if not is_signed:
            _logger.debug(
                "NDA not yet signed for applicant %d (%s), state=%s",
                self.id, self.partner_name, sign_request.state,
            )
            return False

        # NDA signed — advance pipeline
        _logger.info(
            "NDA signed! Advancing applicant %d (%s) to contrato_firmado",
            self.id, self.partner_name,
        )
        self.wa_chat_state = "contrato_firmado"

        # Move Odoo stage to "Contrato firmado" if it exists
        stage = self.env["hr.recruitment.stage"].sudo().search([
            ("name", "ilike", "Contrato")
        ], limit=1)
        if stage:
            self.stage_id = stage.id

        # Send WA confirmation
        self._send_wa(
            self.partner_phone,
            "Tu acuerdo de confidencialidad ha sido firmado exitosamente ✅\n\n"
            "*Bienvenido/a al equipo de OnRentX* 🎉\n\n"
            "Pronto recibirás los detalles sobre tu primer dia y los siguientes pasos. "
            "¡Estamos muy contentos de tenerte con nosotros! 💪",
        )

        # Post to chatter
        self.with_user(1).message_post(
            body=Markup(
                '<div style="background:#e8f5e9;padding:12px;border-left:4px solid #4CAF50;border-radius:6px;">'
                '<b>✅ NDA firmado</b> — %s avanza a <b>Contrato firmado</b></div>'
            ) % (self.partner_name or "Candidato"),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # Notify Aleix
        self._notify_aleix(
            "✅ *%s* firmó el NDA para el puesto de *%s*. "
            "Ya es candidato confirmado — coordina su primer dia." % (
                self.partner_name or "Candidato",
                self.job_id.name if self.job_id else "?",
            )
        )

        return True

    # ─── Internal helpers ───

    def _get_next_missing_doc(self, collected):
        """Return the key of the next document not yet collected, or None if all done."""
        for doc_key, _, _ in ONBOARDING_DOCS:
            if not collected.get(doc_key):
                return doc_key
        return None
