# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
WhatsApp Chatbot for Recruitment Pre-screening.

State machine (14 states):
  idle -> contacto_inicial -> prescreening -> prescreening_eval
  -> [pasa: videoentrevista_pendiente -> videoentrevista_completada
         -> entrevista_final -> registro_jcf -> onboarding -> contrato_firmado
      no_pasa: rechazado]

  Legacy states (keep for backward compat): pasa_pendiente_jcf, listo_entrevista

Triggered automatically on hr.applicant create() if partner_phone is set.
Also triggered manually from Odoo button 'Iniciar Contacto WA'.
Responses handled by webhook in interview_webhooks.py.
"""

import json
import logging
import re
import time

import requests
from markupsafe import Markup, escape as markup_escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Config loaded from ir.config_parameter at runtime
WASENDER_API_URL = "https://wasenderapi.com/api/send-message"

# Opt-out keywords (detected at top of handle_wa_incoming)
OPT_OUT_KEYWORDS = [
    "no me interesa", "darme de baja", "no quiero",
    "ya no quiero", "baja del proceso", "no continuar",
    "cancelar",
]

WA_CHAT_STATES = [
    # === EXISTING STATES (keep for backward compatibility) ===
    ("idle", "Sin contactar"),
    ("contacto_inicial", "Contacto inicial enviado"),
    ("prescreening", "Pre-screening en curso"),
    ("prescreening_eval", "Evaluando pre-screening"),
    ("pasa_pendiente_jcf", "Pasa - Pendiente comprobante JCF"),
    ("listo_entrevista", "Listo para entrevista"),
    ("rechazado", "Rechazado en pre-screening"),
    ("atendido_humano", "Atendido por humano"),
    # === NEW STATES (Phase 10 pipeline automation) ===
    ("videoentrevista_pendiente", "Video-entrevista pendiente"),
    ("videoentrevista_completada", "Video-entrevista completada"),
    ("entrevista_final", "Entrevista final agendada"),
    ("registro_jcf", "Registro JCF en proceso"),
    ("onboarding", "Onboarding - recopilando documentos"),
    ("contrato_firmado", "Contrato firmado"),
]


class HrApplicantChatbot(models.Model):
    _inherit = "hr.applicant"

    wa_chat_state = fields.Selection(
        WA_CHAT_STATES,
        string="Estado chatbot WA",
        default="idle",
        tracking=True,
    )
    wa_chat_data = fields.Text(
        string="Datos chatbot WA (JSON)",
        default="{}",
    )

    # ─── Create override: auto-start pipeline ───

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to auto-start pipeline for new applicants with phone."""
        records = super().create(vals_list)
        for record in records:
            if record.partner_phone:
                try:
                    record._auto_start_pipeline()
                except Exception as e:
                    _logger.error(
                        "Auto-pipeline failed for applicant %d (%s): %s",
                        record.id, record.partner_name, e,
                    )
                    # Never block record creation because of WA failure
        return records

    def _auto_start_pipeline(self):
        """Auto-send survey + WA bienvenida on applicant creation."""
        self.ensure_one()
        data = self._get_wa_data()

        # Prevent duplicate triggers
        if data.get("auto_pipeline_started"):
            _logger.info(
                "Auto-pipeline already started for applicant %d, skipping", self.id
            )
            return

        # Mark as started immediately to prevent race conditions
        data["auto_pipeline_started"] = True
        self._set_wa_data(data)
        # Removed: cr.commit() inside create() breaks transaction safety

        # 1. Find survey for this job
        survey = self._get_job_survey()
        survey_link = ""
        if survey:
            try:
                survey_link = self._create_survey_invite_link(survey)
                _logger.info(
                    "Survey invite created for applicant %d: %s", self.id, survey_link[:80]
                )
            except Exception as e:
                _logger.warning(
                    "Failed to create survey invite for applicant %d: %s", self.id, e
                )
                # Post warning in chatter
                self.with_user(1).message_post(
                    body=Markup(
                        '<div style="background:#fff3e0;padding:8px;'
                        'border-left:4px solid #FF9800;border-radius:6px;">'
                        '<b>⚠️ No se pudo crear invitación al cuestionario:</b> %s</div>'
                    ) % str(e),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
        else:
            self.with_user(1).message_post(
                body=Markup(
                    '<div style="background:#fff3e0;padding:8px;'
                    'border-left:4px solid #FF9800;border-radius:6px;">'
                    '<b>⚠️ Sin cuestionario:</b> El puesto <b>%s</b> no tiene cuestionario '
                    'asociado. Configúralo en la ficha del puesto.</div>'
                ) % (self.job_id.name if self.job_id else "sin puesto"),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )

        # 2. Build and send WA bienvenida using config_id=4 (San Luis) explicitly
        config = self.env["onrentx.wasender.config"].sudo().browse(4)
        if not config.exists() or not config.api_key:
            config = self._get_wasender_config()

        job_name = self.job_id.name if self.job_id else "una vacante en OnRentX"
        candidate_name = self.partner_name or "candidato/a"

        if survey_link:
            msg = (
                "Hola %s! 👋 Soy el asistente de reclutamiento de OnRentX.\n\n"
                "Recibimos tu solicitud para el puesto de *%s*.\n\n"
                "Para continuar el proceso, por favor completa este cuestionario breve:\n"
                "👉 %s\n\n"
                "Son solo 5 minutos. ¡Mucho éxito! 💪"
            ) % (candidate_name, job_name, survey_link)
        else:
            msg = (
                "Hola %s! 👋 Soy el asistente de reclutamiento de OnRentX.\n\n"
                "Recibimos tu solicitud para el puesto de *%s*.\n\n"
                "En breve nos pondremos en contacto contigo para el siguiente paso. "
                "Si tienes dudas, escríbenos aquí. 😊"
            ) % (candidate_name, job_name)

        sent = self._send_wa_with_config(self.partner_phone, msg, config)
        if sent:
            self.wa_chat_state = "contacto_inicial"
            data = self._get_wa_data()
            data["auto_pipeline_started"] = True
            data["wasender_config_id"] = config.id if config else None
            self._set_wa_data(data)
            _logger.info(
                "Auto-pipeline started for applicant %d (%s)", self.id, self.partner_name
            )
        else:
            _logger.warning(
                "WA bienvenida failed for applicant %d (%s) — phone=%s",
                self.id, self.partner_name, self.partner_phone,
            )

    def _create_survey_invite_link(self, survey):
        """Create a survey invite and return the URL for the candidate."""
        partner = self._get_or_create_partner()
        invite = self.env["survey.invite"].sudo().create({
            "survey_id": survey.id,
            "partner_ids": [(6, 0, [partner.id])],
        })
        invite.action_invite()
        # Return the survey start URL (Odoo 18 compatible)
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        # Odoo 18: access_token is on the survey itself, not on the invite
        if survey.access_token:
            return "%s/survey/start/%s" % (base_url, survey.access_token)
        _logger.warning("Survey %d has no access_token", survey.id)
        return None

    # ─── Core data helpers ───

    def _get_wa_data(self):
        """Get chat data as dict."""
        try:
            return json.loads(self.wa_chat_data or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    def _set_wa_data(self, data):
        """Save chat data as JSON."""
        self.wa_chat_data = json.dumps(data, ensure_ascii=False)

    def _get_wasender_config(self):
        """Get WASender config - prefer San Luis (id=4), then Queretaro, skip Leon."""
        # Always prefer San Luis (config_id=4) for HR recruitment
        config = self.env["onrentx.wasender.config"].sudo().browse(4)
        if config.exists() and config.api_key:
            return config
        # Fallback by name
        for config_name in ["San Luis", "Queretaro", "Leon"]:
            config = self.env["onrentx.wasender.config"].search([
                ("name", "ilike", config_name),
            ], limit=1)
            if config and config.api_key:
                return config
        return self.env["onrentx.wasender.config"].search([
            ("api_key", "!=", False),
        ], limit=1)

    def _send_wa_with_config(self, phone, message, config):
        """Send WhatsApp message via WASender API with specific config."""
        if not config or not config.api_key:
            _logger.error("No WASender config with api_key")
            return False

        clean_phone = re.sub(r'[^\d+]', '', phone)
        if not clean_phone.startswith("+"):
            clean_phone = "+52" + clean_phone

        try:
            url = "https://wasenderapi.com/api/send-message"
            resp = requests.post(
                url,
                json={"to": clean_phone, "text": message},
                headers={
                    "Authorization": "Bearer %s" % config.api_key,
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            success = resp.status_code in (200, 201)
            if success:
                _logger.info("WA bot sent to %s via %s: %s", clean_phone, config.name, message[:50])
            else:
                _logger.error("WA bot send failed via %s: %s %s", config.name, resp.status_code, resp.text[:200])

            # Log to chatter
            body = Markup(
                '<div style="background:#dcf8c6;padding:8px 12px;border-radius:8px;'
                'border-left:4px solid #25D366;margin:4px 0;">'
                '<b>🤖 Bot WA enviado</b> (%s) → %s<br/>%s</div>'
            ) % (config.name, clean_phone, Markup('<br/>').join(markup_escape(str(message)).split('\n')))
            self.with_user(1).message_post(
                body=body,
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            return success
        except Exception as e:
            _logger.error("WA bot send error: %s", e)
            return False

    def _send_wa(self, phone, message):
        """Send WA using the config saved in wa_chat_data, or fallback to config_id=4."""
        data = self._get_wa_data()
        config_id = data.get("wasender_config_id")
        config = None
        if config_id:
            config = self.env["onrentx.wasender.config"].sudo().browse(config_id)
        if not config or not config.exists() or not config.api_key:
            config = self._get_wasender_config()
        return self._send_wa_with_config(phone, message, config)

    def _send_wa_rejection(self, score=None):
        """Send professional rejection WA to candidate."""
        candidate_name = self.partner_name or "candidato/a"
        job_name = self.job_id.name if self.job_id else "la vacante"
        score_text = ""
        if score is not None:
            score_text = " Tu perfil obtuvo una puntuación de %s/5 en el cuestionario." % score

        msg = (
            "Hola %s, gracias por tu interés en OnRentX y en el puesto de *%s* 🙏\n\n"
            "Después de revisar tu perfil,%s en esta ocasión no continuaremos "
            "con el proceso de selección.\n\n"
            "No te desanimes, seguimos creciendo y en el futuro podríamos "
            "tener una oportunidad que encaje mejor con tu perfil. 💪\n\n"
            "¡Mucho éxito en tu búsqueda!"
        ) % (candidate_name, job_name, score_text)

        if self.partner_phone:
            self._send_wa(self.partner_phone, msg)

    def _send_flowmingo_video_interview_link(self, data):
        """Send Flowmingo video interview link to candidate and set state to videoentrevista_pendiente."""
        from datetime import datetime, timezone

        ICP = self.env["ir.config_parameter"].sudo()
        flowmingo_url = (
            (self.job_id.flowmingo_interview_url if self.job_id else "")
            or ICP.get_param("onrentx.flowmingo_interview_url", "")
        )

        candidate_name = (self.partner_name or "").split()[0] or "candidato/a"
        job_name = self.job_id.name if self.job_id else "la vacante"

        if flowmingo_url:
            msg = (
                "¡Excelente %s! 🎉 Pasaste la primera evaluación para *%s*.\n\n"
                "El siguiente paso es una video-entrevista corta (*5 minutos*) "
                "desde tu celular o computadora:\n\n"
                "👉 %s\n\n"
                "⏰ Por favor complétala *hoy mismo* — el proceso avanza rápido.\n\n"
                "📌 *Próximos pasos:*\n"
                "1️⃣ Completa la video-entrevista (link arriba)\n"
                "2️⃣ Regístrate en el programa Jóvenes Construyendo el Futuro:\n"
                "   jovenesconstruyendoelfuturo.stps.gob.mx\n"
                "3️⃣ La plataforma OnRentX abre el *1 de Junio* — ahí verás cuándo iniciarías labores\n"
                "4️⃣ Si avanzas, te enviaremos un link para entrevista directa con el director\n\n"
                "Si tienes algún problema técnico escribe *ayuda* y te apoyamos. ¡Mucho éxito! 💪"
            ) % (candidate_name, job_name, flowmingo_url)
        else:
            # No URL configured yet — notify Aleix and still move state
            msg = (
                "¡Excelente %s! 🎉 Pasaste la primera evaluación para el puesto de *%s*.\n\n"
                "En breve te enviaremos el link para tu video-entrevista. "
                "¡Estate pendiente! 📱"
            ) % (candidate_name, job_name)
            # Notify Aleix that URL is not configured
            self._notify_aleix(
                "⚠️ *%s* pasó pre-screening para *%s* pero no hay URL de Flowmingo configurada.\n"
                "Configura: ir.config_parameter 'onrentx.flowmingo_interview_url'" % (
                    self.partner_name, job_name
                )
            )

        if self.partner_phone:
            self._send_wa(self.partner_phone, msg)

        # Set state and record timestamp
        self.wa_chat_state = "videoentrevista_pendiente"
        now_iso = datetime.now(timezone.utc).isoformat()
        data["flowmingo_sent_at"] = now_iso
        data["flowmingo_reminded"] = False
        if flowmingo_url:
            data["flowmingo_url"] = flowmingo_url
        self._set_wa_data(data)

        _logger.info(
            "Flowmingo video interview link sent to applicant %d (%s) at %s",
            self.id, self.partner_name, now_iso,
        )

    def _call_llm(self, prompt, max_tokens=2000):
        """Call LiteLLM (Groq) and return response text."""
        import urllib.request
        ICP = self.env["ir.config_parameter"].sudo()
        litellm_url = ICP.get_param("onrentx.recruitment.litellm_url", "http://159.54.142.132:4000/v1/chat/completions")
        litellm_key = ICP.get_param("onrentx.recruitment.litellm_api_key", "")
        litellm_model = ICP.get_param("onrentx.recruitment.litellm_model", "groq-llama")
        try:
            payload = json.dumps({
                "model": litellm_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.3,
            })
            req = urllib.request.Request(litellm_url, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer %s" % litellm_key)
            req.data = payload.encode()
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            _logger.error("LLM call failed: %s", e)
            return None

    # ─── Actions from Odoo UI ───

    def action_start_wa_contact(self):
        """Button: Open wizard to choose sender then start chatbot."""
        self.ensure_one()

        if not self.partner_phone:
            raise UserError(_("Este candidato no tiene número de teléfono."))

        if self.wa_chat_state not in ("idle", "rechazado"):
            raise UserError(_(
                "El chatbot ya está en estado '%s'. "
                "Use 'Reiniciar Chatbot' para empezar de nuevo."
            ) % dict(WA_CHAT_STATES).get(self.wa_chat_state, self.wa_chat_state))

        return {
            "name": _("Iniciar Pre-screening WhatsApp"),
            "type": "ir.actions.act_window",
            "res_model": "wa.chatbot.start.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_applicant_id": self.id,
            },
        }

    def _start_wa_chatbot(self, wasender_config):
        """Actually start the chatbot with the selected sender config."""
        self.ensure_one()

        # Store selected config for this conversation
        data = self._get_wa_data()
        data["wasender_config_id"] = wasender_config.id if wasender_config else None
        data["conversation"] = []
        data["turn_count"] = 0
        self._set_wa_data(data)

        # Send first message - go straight to screening
        job_name = self.job_id.name if self.job_id else "una vacante"
        candidate_name = self.partner_name or "candidato/a"

        msg = (
            "Hola %s 👋 Soy el asistente de reclutamiento de OnRentX, "
            "plataforma de renta de maquinaria pesada en México.\n\n"
            "Vi tu postulación para *%s* y tus respuestas al cuestionario. "
            "Me gustaría platicar un poco más contigo sobre el puesto. "
            "Son unos 5 minutos. ¿Estás disponible?\n\n"
            "📌 _Dato: para la entrevista presencial necesitarás estar registrado "
            "en Jóvenes Construyendo el Futuro (jovenesconstruyendoelfuturo.stps.gob.mx). "
            "Ve preparándolo si aún no lo tienes._"
        ) % (candidate_name, job_name)

        if self._send_wa_with_config(self.partner_phone, msg, wasender_config):
            self.wa_chat_state = "contacto_inicial"
        else:
            raise UserError(_("Error enviando WhatsApp. Verifique el número y la configuración de WASender."))

    def action_reset_wa_chatbot(self):
        """Reset chatbot state to allow re-contact."""
        self.ensure_one()
        self.wa_chat_state = "idle"
        self._set_wa_data({})
        self.message_post(
            body=Markup('<div style="background:#fff3e0;padding:8px;border-left:4px solid #FF9800;border-radius:6px;">'
                        '<b>🔄 Chatbot WA reiniciado</b></div>'),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

    # ─── Candidate context builder ───

    def _build_candidate_context(self):
        """Build full context about candidate for LLM conversations."""
        job = self.job_id
        job_name = job.name if job else "No especificado"
        job_desc = ""
        if job and job.website_description:
            job_desc = re.sub(r'<[^>]+>', '', job.website_description)[:2000]

        # CV info
        cv_text = "[Sin CV]"
        attachments = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "hr.applicant"),
            ("res_id", "=", self.id),
            ("mimetype", "=", "application/pdf"),
        ], limit=1)
        if attachments:
            cv_text = "[CV adjunto: %s]" % attachments[0].name

        # Survey responses
        survey_text = ""
        try:
            responses = self.response_ids.filtered(lambda r: r.state == "done")
            if responses:
                for line in responses[0].user_input_line_ids:
                    q = line.question_id.title if line.question_id else "?"
                    a = ""
                    if line.suggested_answer_id:
                        a = line.suggested_answer_id.value
                    elif line.value_text_box:
                        a = line.value_text_box
                    elif line.value_char_box:
                        a = line.value_char_box
                    if a:
                        survey_text += "P: %s\nR: %s\n" % (q, a)
        except Exception:
            pass
        if not survey_text:
            survey_text = "[Sin cuestionario completado]"

        return {
            "job_name": job_name,
            "job_desc": job_desc,
            "cv": cv_text,
            "survey": survey_text,
            "candidate_name": self.partner_name or "Candidato",
        }

    # ─── Opt-out handler (called at top of handle_wa_incoming) ───

    def _check_opt_out(self, text, data):
        """
        Check for opt-out keywords or pending opt-out confirmation.
        Returns True if message was handled (caller should stop processing).
        """
        text_lower = text.lower().strip()

        # Handle pending opt-out confirmation
        if data.get("pending_opt_out"):
            if text_lower in ("si", "sí", "yes", "s"):
                # Confirmed opt-out
                data["opted_out"] = True
                data["pending_opt_out"] = False
                self._set_wa_data(data)

                # Move to rechazado stage
                rechazado_stage = self.env["hr.recruitment.stage"].sudo().search([
                    "|",
                    ("name", "ilike", "Rechaz"),
                    ("name", "ilike", "No pas"),
                ], limit=1)
                if rechazado_stage:
                    self.stage_id = rechazado_stage.id

                self.wa_chat_state = "rechazado"

                self._send_wa(
                    self.partner_phone,
                    "Entendido 👍 Te damos de baja del proceso de selección. "
                    "Si en el futuro quieres aplicar de nuevo, estamos aquí. "
                    "¡Mucho éxito! 🙌",
                )
                _logger.info(
                    "Opt-out confirmed for applicant %d (%s)", self.id, self.partner_name
                )
                return True
            else:
                # Not confirmed - clear pending opt-out and continue normal flow
                data["pending_opt_out"] = False
                self._set_wa_data(data)
                return False

        # Check for opt-out keywords
        if any(kw in text_lower for kw in OPT_OUT_KEYWORDS):
            data["pending_opt_out"] = True
            self._set_wa_data(data)
            self._send_wa(
                self.partner_phone,
                "¿Seguro que quieres salir del proceso? "
                "Responde *SI* para confirmar o cualquier otra cosa para continuar.",
            )
            _logger.info(
                "Opt-out keyword detected for applicant %d (%s)", self.id, self.partner_name
            )
            return True

        return False

    def _get_or_create_partner(self):
        """Get or create res.partner for this applicant."""
        if self.partner_id:
            return self.partner_id
        partner = self.env["res.partner"].sudo().create({
            "name": self.partner_name or "Candidato",
            "email": self.email_from,
            "phone": self.partner_phone,
        })
        self.partner_id = partner.id
        return partner

    # ─── Incoming message handler (called from webhook) ───

    def handle_wa_incoming(self, text, message_id=None):
        """Process incoming WA message based on current chat state."""
        # Message dedup
        if message_id:
            data_check = self._get_wa_data()
            processed = data_check.get("processed_msg_ids", [])
            if message_id in processed:
                _logger.info("Dedup: message %s already processed for applicant %d", message_id, self.id)
                return
            processed.append(message_id)
            processed = processed[-20:]  # keep last 20
            data_check["processed_msg_ids"] = processed
            self._set_wa_data(data_check)
            self.env.cr.commit()

        # Lock the row to prevent race conditions from duplicate webhooks
        try:
            self.env.cr.execute(
                "SELECT wa_chat_state FROM hr_applicant WHERE id = %s FOR UPDATE NOWAIT",
                [self.id]
            )
        except Exception:
            _logger.info("Lock failed for applicant %d, skipping (dedup)", self.id)
            return
        row = self.env.cr.fetchone()
        state = row[0] if row else self.wa_chat_state

        # Skip if already in terminal state or evaluating
        if state in ("rechazado", "prescreening_eval", "contrato_firmado"):
            _logger.info("Chatbot skip: applicant %d in terminal state %s", self.id, state)
            return

        data = self._get_wa_data()

        # === OPT-OUT CHECK (top priority, before any state routing) ===
        if self._check_opt_out(text, data):
            return

        # Skip opted-out candidates
        if data.get("opted_out"):
            _logger.info("Skipping message for opted-out applicant %d", self.id)
            return

        if state == "contacto_inicial":
            self._handle_initial_response(text, data)
        elif state == "prescreening":
            self._handle_conversational_turn(text, data)
        elif state == "pasa_pendiente_jcf":
            self._handle_comprobante_jcf(text, data)
        elif state == "listo_entrevista":
            self._handle_faq_response(text, data, state)
        elif state == "videoentrevista_pendiente":
            self._handle_video_interview_pending(text, data)
        elif state == "onboarding":
            self._handle_onboarding_state(text, data, message_id=message_id)
        elif state == "entrevista_final":
            self._handle_entrevista_final(text, data)
        elif state in ("videoentrevista_completada", "registro_jcf"):
            self._handle_faq_response(text, data, state)
        elif state == "idle":
            self._handle_idle_response(text, data)

    def _handle_video_interview_pending(self, text, data):
        """Handle incoming messages while candidate has a pending Flowmingo video interview."""
        text_lower = text.lower().strip()
        flowmingo_url = data.get("flowmingo_url") or (
            (self.job_id.flowmingo_interview_url if self.job_id else "")
            or self.env["ir.config_parameter"].sudo()
            .get_param("onrentx.flowmingo_interview_url", "")
        )

        # "ayuda" keyword → escalate to human
        if "ayuda" in text_lower or "problema" in text_lower or "no puedo" in text_lower:
            if flowmingo_url:
                self._send_wa(
                    self.partner_phone,
                    "Entendido, te comunico con el equipo para ayudarte con la video-entrevista. "
                    "Te contactarán pronto por este medio. 👍\n\n"
                    "Mientras tanto, puedes intentar desde otro dispositivo o navegador:\n"
                    "👉 %s" % flowmingo_url,
                )
            else:
                self._send_wa(
                    self.partner_phone,
                    "Entendido, te comunico con el equipo para ayudarte. "
                    "Te contactarán pronto por este medio. 👍",
                )
            # Notify Aleix
            self._notify_aleix(
                "🆘 *%s* (%s) necesita ayuda con su video-entrevista Flowmingo.\n"
                "Mensaje: %s" % (
                    self.partner_name,
                    self.job_id.name if self.job_id else "?",
                    text[:200],
                )
            )
            self.with_user(1).message_post(
                body=Markup(
                    '<div style="background:#ffcdd2;padding:8px;border-left:4px solid #f44336;border-radius:6px;">'
                    '<b>🆘 %s necesita ayuda con video-entrevista Flowmingo</b><br/>Mensaje: %s</div>'
                ) % (self.partner_name or "", text),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            return

        # Any other message → remind of pending video interview
        if flowmingo_url:
            self._send_wa(
                self.partner_phone,
                "Tu video-entrevista está pendiente 🎬 Grábala cuando estés listo/a:\n\n"
                "👉 %s\n\n"
                "Son solo 5 minutos desde tu celular o computadora. "
                "Si tienes problemas técnicos, escribe *ayuda*." % flowmingo_url,
            )
        else:
            self._send_wa(
                self.partner_phone,
                "Tu video-entrevista está pendiente 🎬 "
                "En breve te enviaremos el link para grabarla. "
                "Si tienes dudas, escribe *ayuda*.",
            )

        # Log to chatter
        self.with_user(1).message_post(
            body=Markup(
                '<div style="background:#fff9c4;padding:8px;border-left:3px solid #f9a825;border-radius:4px;">'
                "📱 WhatsApp recibido de %s (estado: videoentrevista_pendiente):<br/>%s</div>"
            ) % (self.partner_name or "", text),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

    def _handle_onboarding_state(self, text, data, message_id=None):
        """
        Handle incoming messages when candidate is in 'onboarding' state.

        Expected flow:
          - Bot sends document requests one by one (comprobante, CURP, INE frente, INE vuelta)
          - Candidate sends photos → bot attaches them and requests next doc
          - After all docs: bot sends NDA link
          - NDA completion detected by cron (_check_nda_signed)

        Message types:
          - Image/media: attach as onboarding doc, request next
          - Text "ayuda": notify Aleix for human support
          - Other text: remind of pending doc
        """
        text_lower = text.lower().strip()

        # "ayuda" → escalate to human
        if "ayuda" in text_lower or "problema" in text_lower or "no puedo" in text_lower:
            self._send_wa(
                self.partner_phone,
                "Entendido, te comunico con el equipo para ayudarte con los documentos. "
                "Te contactarán pronto por este medio. 👍",
            )
            self._notify_aleix(
                "🆘 *%s* (%s) necesita ayuda durante onboarding.\nMensaje: %s" % (
                    self.partner_name,
                    self.job_id.name if self.job_id else "?",
                    text[:200],
                )
            )
            self.with_user(1).message_post(
                body=Markup(
                    '<div style="background:#ffcdd2;padding:8px;border-left:4px solid #f44336;border-radius:6px;">'
                    '<b>🆘 %s necesita ayuda durante onboarding</b><br/>Mensaje: %s</div>'
                ) % (self.partner_name or "", text),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            return

        # MEDIA CONTRACT: The WaSender webhook controller must set
        # wa_chat_data["pending_media_url"] = mediaUrl BEFORE calling
        # handle_wa_incoming() when the incoming message contains an image.
        # If this field is not set, image messages are treated as text.
        # See: controllers/main.py or webhook handler that calls handle_wa_incoming.
        pending_media_url = data.get("pending_media_url")

        if pending_media_url:
            # Clear the pending media URL before processing
            data.pop("pending_media_url", None)
            self._set_wa_data(data)
            # Auto-detect which doc to attach and proceed
            self._attach_onboarding_document("auto", pending_media_url)
            return

        # Text message — determine which doc is pending and remind
        from .hr_applicant_onboarding import ONBOARDING_DOCS
        collected = data.get("onboarding_docs", {})
        next_doc = next(
            ((key, label, msg) for key, label, msg in ONBOARDING_DOCS if not collected.get(key)),
            None,
        )

        if next_doc:
            doc_key, doc_label, doc_msg = next_doc
            self._send_wa(
                self.partner_phone,
                "En este momento necesito que envies tu *%s* como foto 📷\n\n%s\n\n"
                "Si tienes dudas, escribe *ayuda*." % (doc_label, doc_msg),
            )
        else:
            # All docs collected but still in onboarding — NDA pending
            nda_sent = data.get("nda_sent")
            if nda_sent:
                sign_url = None
                sign_request_id = data.get("nda_sign_request_id")
                if sign_request_id:
                    try:
                        sr = self.env["sign.oca.request"].sudo().browse(sign_request_id)
                        if sr.exists() and sr.request_item_ids:
                            base_url = self.env["ir.config_parameter"].sudo().get_param(
                                "web.base.url", "https://odoo.tramarental.com"
                            )
                            url = sr.request_item_ids[0].access_url
                            if url and not url.startswith("http"):
                                url = base_url.rstrip("/") + url
                            sign_url = url
                    except Exception:
                        pass

                if sign_url:
                    self._send_wa(
                        self.partner_phone,
                        "Todos tus documentos fueron recibidos ✅\n\n"
                        "Solo falta que firmes el acuerdo de confidencialidad:\n\n"
                        "📝 %s\n\n"
                        "Si tienes dudas, escribe *ayuda*." % sign_url,
                    )
                else:
                    self._send_wa(
                        self.partner_phone,
                        "Todos tus documentos fueron recibidos ✅\n\n"
                        "El siguiente paso es la firma del acuerdo de confidencialidad. "
                        "En breve te enviamos el enlace. 📝",
                    )
            else:
                # Docs done, NDA not sent yet — trigger now
                self._send_nda_sign_request()

        # Log to chatter
        self.with_user(1).message_post(
            body=Markup(
                '<div style="background:#fff9c4;padding:8px;border-left:3px solid #f9a825;border-radius:4px;">'
                "📱 WhatsApp recibido de %s (estado: onboarding):<br/>%s</div>"
            ) % (self.partner_name or "", text),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

    def _handle_jcf_response(self, text, data):
        """Handle JCF registration confirmation."""
        text_lower = text.lower().strip()
        negative = any(w in text_lower for w in ["no", "aún no", "todavia", "aun no"])

        if negative:
            self._send_wa(
                self.partner_phone,
                "Para continuar necesitas registrarte en el programa JCF.\n\n"
                "📌 Regístrate aquí: jovenesconstruyendoelfuturo.stps.gob.mx\n\n"
                "Cuando estés registrado/a, escríbenos y continuamos con el proceso. 👍"
            )
        else:
            # Start conversational pre-screening
            data["conversation"] = []
            data["turn_count"] = 0
            self._set_wa_data(data)
            self.wa_chat_state = "prescreening"

            self._send_wa(
                self.partner_phone,
                "Perfecto ✅ Ahora platicaremos un poco sobre tu experiencia "
                "y el puesto. Son unos 5 min. ¡Vamos! 💪"
            )

            # Generate first question based on full context
            self._send_next_conversational_turn(data)

    def _handle_conversational_turn(self, text, data):
        """Handle a conversational turn during pre-screening."""
        # Check for stuck loop
        if self._check_stuck_loop(data, "prescreening"):
            pass  # Auto-corrected, continue normally
        conversation = data.get("conversation", [])
        turn_count = data.get("turn_count", 0)

        # Save candidate's response
        conversation.append({"role": "candidate", "text": text})
        turn_count += 1
        data["conversation"] = conversation
        data["turn_count"] = turn_count
        self._set_wa_data(data)

        # After 5-7 turns, evaluate
        if turn_count >= 5:
            # Ask LLM if we have enough info or need more
            should_continue = self._should_continue_screening(data)
            if not should_continue or turn_count >= 7:
                self.wa_chat_state = "prescreening_eval"
                self._send_wa(
                    self.partner_phone,
                    "Gracias por la plática 🙏\n\n"
                    "Estamos evaluando tu perfil. Te avisaremos los siguientes pasos pronto."
                )
                self._evaluate_conversational_screening(data)
                return

        # Generate next question based on conversation so far
        self._send_next_conversational_turn(data)

    def _send_next_conversational_turn(self, data):
        """Generate and send next conversational question using LLM."""
        context = self._build_candidate_context()
        conversation = data.get("conversation", [])
        turn_count = data.get("turn_count", 0)

        # Build conversation history for LLM
        conv_text = ""
        for msg in conversation:
            role = "Candidato" if msg["role"] == "candidate" else "Bot"
            conv_text += "%s: %s\n" % (role, msg["text"])

        prompt = """Eres el asistente de reclutamiento de OnRentX por WhatsApp. Estás haciendo pre-screening a un candidato.

CONTEXTO DEL PUESTO:
- Puesto: %s
- Descripción: %s

DATOS DEL CANDIDATO:
- Nombre: %s
- CV: %s
- Cuestionario previo:
%s

CONVERSACIÓN HASTA AHORA:
%s

TURNO ACTUAL: %d de 5-7

INSTRUCCIONES:
- Genera LA SIGUIENTE PREGUNTA basándote en lo que el candidato ha dicho hasta ahora.
- Si el candidato dio una respuesta vaga, profundiza pidiendo un ejemplo concreto.
- Si mencionó algo interesante, pregunta más detalles.
- Si el cuestionario tiene info, cruza con lo que dice en la conversación.
- Las preguntas deben ser naturales, como una conversación por WhatsApp (cortas, directas).
- NO repitas preguntas que ya hiciste.
- Evalúa requisitos del puesto que aún no se han tocado.
- Si es el primer turno, empieza preguntando por su experiencia más relevante para el puesto.

Responde SOLO con la pregunta (1-3 líneas máximo), sin explicaciones ni prefijos.""" % (
            context["job_name"],
            context["job_desc"][:1000],
            context["candidate_name"],
            context["cv"],
            context["survey"][:800],
            conv_text,
            turn_count + 1,
        )

        _logger.info("Calling LLM for next question (applicant %d, turn %d)...", self.id, data.get("turn_count", 0))
        response = self._call_llm(prompt, max_tokens=200)
        if response:
            question = response.strip().strip('"').strip("'")
            _logger.info("LLM generated question: %s", question[:80])
            # Save bot's question in conversation
            data["conversation"].append({"role": "bot", "text": question})
            self._set_wa_data(data)
            # Commit before sending WA to ensure state is saved
            self.env.cr.commit()
            sent = self._send_wa(self.partner_phone, question)
            _logger.info("WA send result: %s", sent)
        else:
            _logger.error("LLM failed to generate question for applicant %d", self.id)

    def _should_continue_screening(self, data):
        """Ask LLM if we have enough info or need more questions."""
        context = self._build_candidate_context()
        conversation = data.get("conversation", [])

        conv_text = "\n".join([
            "%s: %s" % ("Candidato" if m["role"] == "candidate" else "Bot", m["text"])
            for m in conversation
        ])

        prompt = """Basándote en esta conversación de pre-screening para el puesto de "%s", ¿tenemos suficiente información para evaluar al candidato o necesitamos más preguntas?

Conversación:
%s

Requisitos del puesto:
%s

Responde SOLO "SI" si necesitamos más preguntas, o "NO" si ya tenemos suficiente.""" % (
            context["job_name"], conv_text, context["job_desc"][:500]
        )

        response = self._call_llm(prompt, max_tokens=10)
        if response:
            return "si" in response.lower().strip()
        return False

    def _evaluate_conversational_screening(self, data):
        """Evaluate the full conversational pre-screening."""
        context = self._build_candidate_context()
        conversation = data.get("conversation", [])

        conv_text = "\n".join([
            "%s: %s" % ("Candidato" if m["role"] == "candidate" else "Entrevistador", m["text"])
            for m in conversation
        ])

        prompt = """Evalúa la conversación de pre-screening por WhatsApp de este candidato para el puesto de "%s" en OnRentX.

PUESTO: %s
DESCRIPCIÓN: %s
CV: %s
CUESTIONARIO PREVIO:
%s

CONVERSACIÓN COMPLETA:
%s

EVALÚA:
1. Score por cada requisito del puesto (1-5) con evidencia de la conversación (cita textual)
2. Score general (1-5)
3. Banderas rojas (respuestas vagas, inconsistencias con cuestionario, problemas JCF/ubicación/edad)
4. Veredicto: PASA (score >= 3) / NO PASA (score < 3) / CON RESERVAS

Responde en formato JSON:
{
  "scores": [{"requirement": "requisito del puesto", "score": X, "evidence": "cita textual del candidato"}],
  "overall_score": X,
  "red_flags": ["flag1", "flag2"],
  "verdict": "PASA|NO_PASA|CON_RESERVAS",
  "summary": "resumen en 2 líneas"
}""" % (
            context["job_name"], context["job_name"],
            context["job_desc"][:1000], context["cv"],
            context["survey"][:800], conv_text,
        )

        response = self._call_llm(prompt, max_tokens=1000)
        if not response:
            _logger.error(
                "Pre-screening eval LLM failed for applicant %d (%s) — applying fallback",
                self.id, self.partner_name,
            )
            self._notify_aleix(
                "⚠️ *%s* (%s): LLM falló en evaluación pre-screening. "
                "Enviado a video-entrevista para revisión manual." % (
                    self.partner_name or "?",
                    self.job_id.name if self.job_id else "?",
                )
            )
            self.with_user(1).message_post(
                body=Markup(
                    '<div style="background:#ffcdd2;padding:8px;border-left:4px solid #f44336;border-radius:6px;">'
                    '<b>⚠️ Error LLM en evaluación pre-screening</b> — LLM no respondió.<br/>'
                    'Candidato enviado a video-entrevista Flowmingo para revisión manual. '
                    'Revisar perfil manualmente antes de avanzar.</div>'
                ),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            self._send_flowmingo_video_interview_link(data)
            return

        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                eval_data = json.loads(match.group())
            else:
                eval_data = {"verdict": "CON_RESERVAS", "overall_score": 3, "summary": "Error en evaluación"}
        except (json.JSONDecodeError, TypeError):
            eval_data = {"verdict": "CON_RESERVAS", "overall_score": 3, "summary": "Error parsing evaluación"}

        verdict = eval_data.get("verdict", "CON_RESERVAS")
        overall_score = eval_data.get("overall_score", 3)
        summary = eval_data.get("summary", "")
        red_flags = eval_data.get("red_flags", [])
        scores = eval_data.get("scores", [])

        # Build HTML for chatter
        body_html = (
            '<div style="background:#e8eaf6;padding:12px;'
            'border-left:4px solid #3F51B5;border-radius:8px;">'
            '<h4>🤖 Evaluación Pre-screening WA</h4>'
            '<p><b>Puesto:</b> %s | <b>Score:</b> %s/5 | '
            '<b>Veredicto: %s</b></p>'
        ) % (
            markup_escape(str(context["job_name"])),
            markup_escape(str(overall_score)),
            markup_escape(str(verdict)),
        )

        if scores:
            body_html += '<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;width:100%%;">'
            body_html += '<tr style="background:#f0f0f0;"><th>Requisito</th><th>Score</th><th>Evidencia</th></tr>'
            for s in scores:
                body_html += '<tr><td>%s</td><td>%s/5</td><td>%s</td></tr>' % (
                    markup_escape(str(s.get("requirement", s.get("question", "?")))),
                    markup_escape(str(s.get("score", "?"))),
                    markup_escape(str(s.get("evidence", s.get("reason", "")))),
                )
            body_html += '</table>'

        if red_flags:
            body_html += '<h4>Banderas rojas:</h4><ul>'
            for flag in red_flags:
                body_html += '<li>%s</li>' % markup_escape(str(flag))
            body_html += '</ul>'

        if summary:
            body_html += '<p><b>Resumen:</b> %s</p>' % markup_escape(str(summary))

        body_html += '</div>'

        self.with_user(1).message_post(
            body=Markup(body_html),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # Score-based decision (minimum 2/5 to pass — AIS-106)
        MIN_SCORE = 2.0
        try:
            score_num = float(overall_score)
        except (ValueError, TypeError):
            score_num = 0

        # Calculate ranking for this position
        ranking_text = ""
        if self.job_id:
            other_passed = self.env["hr.applicant"].sudo().search([
                ("job_id", "=", self.job_id.id),
                ("id", "!=", self.id),
                ("wa_chat_state", "in", ["pasa_pendiente_jcf", "listo_entrevista", "videoentrevista_pendiente"]),
            ])
            total_for_job = len(other_passed) + (1 if score_num >= MIN_SCORE else 0)
            if total_for_job > 0:
                ranking_text = " | Candidatos que pasan para %s: %d" % (
                    self.job_id.name, total_for_job
                )

        # Add ranking to chatter
        if ranking_text:
            self.with_user(1).message_post(
                body=Markup(
                    '<div style="background:#e3f2fd;padding:8px;border-left:4px solid #2196F3;border-radius:6px;">'
                    '<b>📊 Ranking:</b> Score %s/5%s</div>'
                ) % (overall_score, ranking_text),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )

        if score_num < MIN_SCORE or verdict == "NO_PASA":
            self.wa_chat_state = "rechazado"
            self._send_wa_rejection(score_num)
        else:
            # PASA (score >= 3) → send Flowmingo video interview link
            self._send_flowmingo_video_interview_link(data)

        _logger.info(
            "Pre-screening eval for %d (%s): %s (score=%s)",
            self.id, self.partner_name, verdict, overall_score,
        )

    # ─── Intelligent initial response handler ───

    def _handle_initial_response(self, text, data):
        """Handle first response from candidate using LLM."""
        context = self._build_candidate_context()
        prompt = """Eres el asistente de reclutamiento de OnRentX por WhatsApp. El candidato %s respondió a tu primer mensaje sobre el puesto de %s.

Su respuesta: "%s"

REGLAS:
- Si dice "sí", "ok", "claro", "disponible" o similar → responde "Perfecto, vamos con unas preguntas rápidas" y añade START_SCREENING al final
- Si dice "no puedo ahora", "estoy ocupado" → responde amablemente, menciona que el proceso cierra pronto, pregunta cuándo puede
- Si hace una pregunta → contéstala brevemente y re-pregunta si está disponible
- Si dice "ya hablé con alguien" o similar → di "Solo completamos unas preguntas rápidas para tu expediente"
- Responde CORTO (2-3 líneas máximo), natural, como WhatsApp
- Si el candidato está listo, AÑADE la palabra START_SCREENING al final de tu respuesta""" % (
            context["candidate_name"], context["job_name"], text)

        response = self._call_llm(prompt, max_tokens=200)
        if response and "START_SCREENING" in response:
            response = response.replace("START_SCREENING", "").strip()
            self._send_wa(self.partner_phone, response)
            data["conversation"] = []
            data["turn_count"] = 0
            self._set_wa_data(data)
            self.wa_chat_state = "prescreening"
            self.env.cr.commit()
            # import time removed (sleep removed)
            # Removed: time.sleep(6) blocks worker thread
            pass
            self._send_next_conversational_turn(data)
        elif response:
            self._send_wa(self.partner_phone, response)
            # Log to chatter
            self.with_user(1).message_post(
                body=Markup(
                    '<div style="background:#fff9c4;padding:8px;border-left:3px solid #f9a825;border-radius:4px;">'
                    '📱 WhatsApp recibido de %s:<br/>%s</div>'
                ) % (self.partner_name or '', text),
                message_type='comment', subtype_xmlid='mail.mt_note',
            )

    # ─── JCF Comprobante handler (after Flowmingo PASS) ───

    def _handle_comprobante_jcf(self, text, data):
        """
        Handle messages in pasa_pendiente_jcf state.

        Expected flow:
          - Bot asked for JCF comprobante photo (set by Flowmingo webhook)
          - Candidate sends photo → advance to entrevista_final + send Cal.com link
          - Candidate sends text → remind to send photo

        The Flowmingo webhook stores 'calcom_url' and 'flowmingo_score' in wa_chat_data.
        """
        candidate_first = (self.partner_name or "").split()[0] if self.partner_name else "candidato/a"

        # Check for media (comprobante photo)
        pending_media_url = data.get("pending_media_url")
        if pending_media_url:
            # Save comprobante reference in wa_data — commit data BEFORE changing state (CR-05)
            data.pop("pending_media_url", None)
            data["jcf_comprobante_url"] = pending_media_url
            data["jcf_comprobante_received_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._set_wa_data(data)
            self.env.cr.commit()  # flush before state change — CR-05 atomicity

            # Advance to entrevista_final stage in Odoo
            entrevista_stage = self.env["hr.recruitment.stage"].sudo().search([
                ("name", "ilike", "Entrevista"),
            ], limit=1)
            if entrevista_stage:
                self.stage_id = entrevista_stage.id

            self.wa_chat_state = "entrevista_final"

            # Send Cal.com scheduling link
            calcom_url = data.get(
                "calcom_url",
                self.env["ir.config_parameter"].sudo().get_param(
                    "onrentx.recruitment.calcom_url",
                    "https://cal.com/aleix-onrentx-er3fnp/entrevista-onrentx",
                ),
            )
            score = data.get("flowmingo_score", "")
            try:
                score_text = " de %.1f/10" % float(score) if score else ""
            except (TypeError, ValueError):
                score_text = ""
            self._send_wa(
                self.partner_phone,
                "¡Gracias %s! ✅ Recibimos tu comprobante JCF.\n\n"
                "Superaste la video-entrevista%s y ya tienes todo listo para continuar.\n\n"
                "📅 *Agenda tu entrevista final con Aleix (Director de OnRentX):*\n"
                "%s\n\n"
                "¡Esperamos conocerte pronto! 💪"
                % (candidate_first, score_text, calcom_url),
            )

            # Log to chatter — http/https guard prevents javascript: URIs (MEDIUM fix)
            safe_media_url = str(pending_media_url) if str(pending_media_url).startswith(("http://", "https://")) else "#"
            self.with_user(1).message_post(
                body=Markup(
                    '<div style="background:#e8f5e9;padding:10px;border-left:4px solid #43a047;border-radius:6px;">'
                    '<b>✅ Comprobante JCF recibido</b><br/>'
                    'Candidato avanzado a <b>Entrevista Final</b>. Cal.com enviado.<br/>'
                    '<a href="%s">Ver comprobante</a></div>'
                ) % markup_escape(safe_media_url),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            return

        # No media — remind to send photo
        text_lower = text.lower().strip()
        if any(w in text_lower for w in ["ayuda", "no tengo", "cómo me registro", "como me registro", "no sé", "no se"]):
            self._send_wa(
                self.partner_phone,
                "Para registrarte en el programa JCF sigue estos pasos:\n\n"
                "1️⃣ Entra a: *jovenesconstruyendoelfuturo.stps.gob.mx*\n"
                "2️⃣ Regístrate como *aprendiz*\n"
                "3️⃣ Una vez registrado/a, envíanos una foto de tu comprobante por aquí\n\n"
                "Si tienes problemas con el registro, escríbenos y te ayudamos. 👍",
            )
        else:
            self._send_wa(
                self.partner_phone,
                "Hola %s 👋 Para continuar con tu proceso necesitamos que nos envíes "
                "una *foto de tu comprobante de registro en JCF* (Jóvenes Construyendo el Futuro).\n\n"
                "📸 Envía la foto directamente por este chat.\n\n"
                "Si aún no estás registrado/a: *jovenesconstruyendoelfuturo.stps.gob.mx*"
                % candidate_first,
            )

        self.with_user(1).message_post(
            body=Markup(
                '<div style="background:#fff9c4;padding:8px;border-left:3px solid #f9a825;border-radius:4px;">'
                "📱 WhatsApp recibido de %s (estado: pasa_pendiente_jcf):<br/>%s</div>"
            ) % (self.partner_name or "", text),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

    # ─── AIS-131: Automatic exit from entrevista_final → registro_jcf ───

    def _handle_entrevista_final(self, text, _data):
        """
        When a candidate in entrevista_final sends any message, confirm the interview
        is scheduled and advance them to registro_jcf so the cron monitor picks them up.

        AIS-131: previously this state had no automatic exit and candidates got stuck.
        """
        candidate_first = (self.partner_name or "").split()[0] or "candidato/a"
        job_name = self.job_id.name if self.job_id else "OnRentX"

        # Transition to registro_jcf in Odoo stage
        jcf_stage = self.env["hr.recruitment.stage"].sudo().search([
            ("name", "ilike", "JCF"),
        ], limit=1)
        if jcf_stage:
            self.stage_id = jcf_stage.id

        self.wa_chat_state = "registro_jcf"

        # WR-02: reset last_activity so cron 48h timer starts from NOW, not from entrevista_final
        import time as _time
        _data2 = self._get_wa_data()
        _data2["last_activity"] = _time.strftime("%Y-%m-%dT%H:%M:%S+00:00", _time.gmtime())
        _data2.pop("reminder_sent_registro_jcf", None)
        self._set_wa_data(_data2)

        self._send_wa(
            self.partner_phone,
            "¡Hola %s! 🎉 Tu entrevista final para *%s* ya está confirmada.\n\n"
            "El siguiente paso es completar tu alta en el programa *Jóvenes Construyendo el Futuro (JCF)*. "
            "En breve el equipo te contactará con los detalles.\n\n"
            "Si tienes alguna duda mientras tanto, escríbenos aquí. ¡Mucho éxito! 💪"
            % (candidate_first, job_name),
        )

        # Log to chatter
        self.with_user(1).message_post(
            body=Markup(
                '<div style="background:#e8f5e9;padding:8px;border-left:4px solid #43a047;border-radius:6px;">'
                "✅ <b>%s</b> avanzado/a a <b>registro_jcf</b> (mensaje recibido en entrevista_final).<br/>"
                "Mensaje: %s</div>"
            ) % (self.partner_name or "", text),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        _logger.info(
            "AIS-131: applicant %d (%s) auto-advanced entrevista_final → registro_jcf",
            self.id, self.partner_name,
        )

    # ─── FAQ Agent for JCF and interview stages ───

    def _handle_faq_response(self, text, data, state):
        """Intelligent FAQ agent for candidates in JCF or interview stage."""
        context = self._build_candidate_context()

        # Check for human handoff keywords BEFORE LLM
        text_lower = text.lower()
        human_keywords = ['hablar con alguien', 'hablar con una persona', 'comunicarme con',
                          'humano', 'persona real', 'alguien de la empresa', 'hablar con un humano']
        if any(kw in text_lower for kw in human_keywords):
            self._send_wa(self.partner_phone,
                "Te comunico con el equipo de reclutamiento, te contactarán pronto por este medio. 👍")
            # Notify Aleix
            self._notify_aleix(
                "💬 *%s* (%s) quiere hablar con humano.\nMensaje: %s" % (
                    self.partner_name, context["job_name"], text[:200]))
            self.with_user(1).message_post(
                body=Markup('<div style="background:#ffcdd2;padding:8px;border-left:4px solid #f44336;border-radius:6px;">'
                    '<b>🚨 URGENTE: %s quiere hablar con humano</b><br/>Mensaje: %s</div>'
                ) % (self.partner_name or '', text),
                message_type='comment', subtype_xmlid='mail.mt_note',
            )
            return

        # Build booking info
        booking_info = ""
        if self.booking_id:
            from datetime import timedelta
            mexico_tz = timedelta(hours=-6)
            start = self.booking_id.start
            if start:
                mexico_time = start + mexico_tz
                booking_info = "Entrevista agendada: %s hora México" % mexico_time.strftime("%d/%m/%Y %H:%M")
            if hasattr(self, 'booking_portal_url') and self.booking_portal_url:
                booking_info += "\nLink: %s" % self.booking_portal_url

        empresa_jcf = ""
        if hasattr(self, 'jcf_empresa') and self.jcf_empresa:
            empresa_jcf = self.jcf_empresa

        jcf_prompt = ""
        if state == "registro_jcf":
            jcf_prompt = "- VINCULACIÓN JCF: vincularse a empresa Jorge Martínez Hernández\n- Pasos: 1) Ir a jovenesconstruyendoelfuturo.stps.gob.mx\n  2) Buscar *Jorge Martínez Hernández*\n  3) Solicitar vinculación\n  4) Enviar CAPTURA DE PANTALLA\n- Si no encuentra: buscar alfabéticamente o llamar al 079\n- NO dar otra empresa\n"

        prompt = """Eres el asistente de reclutamiento de OnRentX por WhatsApp. Candidato: %s, puesto: %s.

CONTEXTO:
- Estado: %s
- JCF: $9,582.47/mes, IMSS, 12 meses. Registro: jovenesconstruyendoelfuturo.stps.gob.mx/aprendiz
%s%s%s- Entrevistas digitales por Google Meet con Aleix (CEO)
- Ubicación: San Luis Potosí, zona Garita de Jalisco
- Horario trabajo: se define en la entrevista, somos flexibles
- OnRentX: plataforma de renta de maquinaria pesada, startup en SLP
- Descripción puesto: %s
- Si no sabes algo: "Eso lo platicamos en la entrevista"
- NUNCA inventes fechas, horarios específicos ni agendes entrevistas
- Si tiene booking, dile el link. Si no, dile "te enviaremos el link pronto"
- Responde CORTO (2-3 líneas)

MENSAJE: %s""" % (
            context["candidate_name"], context["job_name"], state,
            "- Empresa JCF: %s\n" % empresa_jcf if empresa_jcf else "",
            "- %s\n" % booking_info if booking_info else "",
            jcf_prompt,
            context["job_desc"][:500], text)

        response = self._call_llm(prompt, max_tokens=300)
        if response:
            self._send_wa(self.partner_phone, response.strip())
        else:
            self._send_wa(self.partner_phone,
                "Gracias por tu mensaje. Si tienes dudas, escríbenos. 😊")

        # Log incoming + response to chatter
        self.with_user(1).message_post(
            body=Markup(
                '<div style="background:#fff9c4;padding:8px;border-left:3px solid #f9a825;border-radius:4px;">'
                '📱 WhatsApp recibido de %s:<br/>%s</div>'
            ) % (self.partner_name or '', text),
            message_type='comment', subtype_xmlid='mail.mt_note',
        )

    # ─── Idle handler ───

    def _handle_idle_response(self, text, data):
        """Handle messages from candidates in idle state (not yet in process)."""
        context = self._build_candidate_context()
        prompt = """Eres el asistente de reclutamiento de OnRentX. Un candidato (%s) que aún no ha iniciado el proceso te escribió: "%s"

Si pregunta sobre el puesto de %s, contesta brevemente.
Si quiere iniciar el proceso, dile que complete el cuestionario que le enviamos por email.
Responde CORTO (2-3 líneas). Sé amable.""" % (
            context["candidate_name"], text, context["job_name"])

        response = self._call_llm(prompt, max_tokens=200)
        if response:
            self._send_wa(self.partner_phone, response.strip())

    # ─── Aleix notification ───

    def _notify_aleix(self, message):
        """Send WA notification to Aleix."""
        config = self._get_wasender_config()
        if not config:
            return
        try:
            requests.post(
                "https://wasenderapi.com/api/send-message",
                json={"to": "+524424751707", "text": message},
                headers={
                    "Authorization": "Bearer %s" % config.api_key,
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
        except Exception as e:
            _logger.error("Aleix notification failed: %s", e)

    # ─── Loop detection and auto-correction ───

    def _check_stuck_loop(self, data, state):
        """Detect if bot is stuck in a loop and auto-correct."""
        conversation = data.get("conversation", [])
        if len(conversation) < 6:
            return False

        # Check last 3 bot messages for repetition
        bot_msgs = [m["text"] for m in conversation[-6:] if m["role"] == "bot"]
        if len(bot_msgs) >= 3:
            from difflib import SequenceMatcher
            for i in range(len(bot_msgs) - 1):
                ratio = SequenceMatcher(None, bot_msgs[i], bot_msgs[-1]).ratio()
                if ratio > 0.7:
                    _logger.warning("Loop detected for applicant %d! Auto-correcting.", self.id)
                    # Send apology and move forward
                    self._send_wa(self.partner_phone,
                        "Disculpa la repetición. Déjame reformular mi pregunta...")
                    # Notify Aleix
                    self._notify_aleix(
                        "⚠️ Bot en bucle con *%s* (%s). Auto-corregido." % (
                            self.partner_name, self.job_id.name if self.job_id else "?"))
                    return True
        return False


    # ─── Chatbot Lifecycle (reminders + auto-close) ───

    @api.model
    def _cron_chatbot_lifecycle(self):
        """Cron: send reminders and auto-close stale conversations.
        Runs every 6 hours. Only sends between 9am-8pm Mexico time.
        FIXED: excludes Deserto/Rechazado/archived candidates and opted-out."""
        from datetime import datetime, timedelta, timezone

        mexico_tz = timezone(timedelta(hours=-6))
        now_mexico = datetime.now(mexico_tz)

        # Only send between 9am and 8pm Mexico time
        if now_mexico.hour < 9 or now_mexico.hour >= 20:
            _logger.info("Lifecycle cron: outside sending hours (%d), skipping", now_mexico.hour)
            return

        now_utc = fields.Datetime.now()

        # Pre-fetch excluded stage IDs (Deserto, Rechazado, No paso)
        excluded_stages = self.env['hr.recruitment.stage'].sudo().search([
            '|', '|',
            ('name', 'ilike', 'Deserto'),
            ('name', 'ilike', 'Rechaz'),
            ('name', 'ilike', 'No pas'),
        ]).ids

        # Config
        REMINDER_HOURS = {
            'contacto_inicial': 24,
            'prescreening': 24,
            'pasa_pendiente_jcf': 48,
            'listo_entrevista': 24,
            'videoentrevista_pendiente': 24,
            'registro_jcf': 48,
            'onboarding': 48,
        }
        CLOSE_HOURS = {
            'contacto_inicial': 48,
            'prescreening': 48,
            'pasa_pendiente_jcf': 168,  # 7 days for JCF registration
            'listo_entrevista': 48,
            'videoentrevista_pendiente': 72,  # 3 days for video interview
            'registro_jcf': 168,
            'onboarding': 168,
        }

        REMINDER_MESSAGES = {
            'contacto_inicial': "Hola %s, te escribimos de OnRentX. ¿Sigues interesado/a en el puesto de %s? El proceso cierra pronto. 😊",
            'prescreening': "Hola %s, ¿pudiste ver mi último mensaje? Estamos con las preguntas del pre-screening para %s. 📝",
            'pasa_pendiente_jcf': "Hola %s, recordatorio: necesitamos tu comprobante de registro en JCF para avanzar con tu entrevista para %s. 📌 Regístrate en jovenesconstruyendoelfuturo.stps.gob.mx",
            'listo_entrevista': "Hola %s, te recordamos que tienes pendiente agendar tu entrevista para %s. ¿Necesitas ayuda? 📅",
            'videoentrevista_pendiente': "Hola %s, recuerda que tienes pendiente tu video-entrevista para %s. ¿Pudiste grabarlo? 🎥",
            'registro_jcf': "Hola %s, ¿cómo va tu registro en JCF para %s? Si tienes dudas, con gusto te ayudo. 💪",
            'onboarding': "Hola %s, recuerda que necesitamos tus documentos para completar el proceso de contratación para %s. 📄",
        }

        for state, reminder_h in REMINDER_HOURS.items():
            close_h = CLOSE_HOURS[state]

            # Find candidates in this state — QUADRUPLE FILTER:
            # 1. Only active candidates
            # 2. Exclude Deserto/Rechazado/No paso stages
            # 3. Only in the specific WA state
            # 4. For JCF states, only JCF candidates
            domain = [
                ('wa_chat_state', '=', state),
                ('active', '=', True),
                ('stage_id', 'not in', excluded_stages),
            ]
            if state in ('registro_jcf', 'pasa_pendiente_jcf'):
                domain.append(('is_jcf_candidate', '=', True))
            candidates = self.env['hr.applicant'].sudo().search(domain)

            for candidate in candidates:
                data = candidate._get_wa_data()

                # Skip opted-out candidates
                if data.get("opted_out"):
                    continue

                last_activity = data.get('last_activity')
                if not last_activity:
                    # Use write_date as fallback
                    last_activity = candidate.write_date.isoformat() if candidate.write_date else None
                if not last_activity:
                    continue

                try:
                    from datetime import datetime as dt
                    last_dt = dt.fromisoformat(last_activity.replace('Z', '+00:00'))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError):
                    continue

                now_aware = now_utc.replace(tzinfo=timezone.utc) if now_utc.tzinfo is None else now_utc
                hours_since = (now_aware - last_dt).total_seconds() / 3600

                # Auto-close
                if hours_since >= close_h:
                    if not data.get('auto_closed'):
                        _logger.info("Auto-closing applicant %d (%s) - %d hours in %s",
                                     candidate.id, candidate.partner_name, int(hours_since), state)

                        # Move to Deserto stage
                        deserto_stage = self.env['hr.recruitment.stage'].sudo().search([
                            ('name', 'ilike', 'Deserto'),
                        ], limit=1)
                        if deserto_stage:
                            candidate.stage_id = deserto_stage.id

                        candidate.wa_chat_state = 'idle'
                        data['auto_closed'] = True
                        data['auto_closed_at'] = now_utc.isoformat()
                        candidate._set_wa_data(data)

                        candidate.with_user(1).message_post(
                            body=Markup(
                                '<div style="background:#fff3e0;padding:8px;border-left:4px solid #FF9800;border-radius:6px;">'
                                '<b>⏰ Conversación cerrada automáticamente</b> - sin respuesta por %d horas en estado %s</div>'
                            ) % (int(hours_since), state),
                            message_type='comment', subtype_xmlid='mail.mt_note',
                        )
                    continue

                # Send reminder (with timestamp dedup to prevent spam on reset)
                if hours_since >= reminder_h:
                    reminder_key = 'reminder_sent_' + state
                    last_reminder_at = data.get(reminder_key + '_at')
                    should_send = True
                    if last_reminder_at:
                        from datetime import datetime as _dt, timezone as _tz
                        try:
                            _lr = _dt.fromisoformat(last_reminder_at.replace('Z', '+00:00'))
                            if _lr.tzinfo is None:
                                _lr = _lr.replace(tzinfo=_tz.utc)
                            _now_aware = now_utc.replace(tzinfo=_tz.utc) if now_utc.tzinfo is None else now_utc
                            hours_since_reminder = (_now_aware - _lr).total_seconds() / 3600
                            # Only send another reminder if at least 2x the reminder interval has passed
                            if hours_since_reminder < reminder_h * 2:
                                should_send = False
                        except (ValueError, TypeError):
                            pass  # If parse fails, send reminder

                    if should_send:
                        job_name = candidate.job_id.name if candidate.job_id else 'la vacante'
                        msg = REMINDER_MESSAGES.get(state, '')
                        if msg and candidate.partner_phone:
                            msg = msg % (candidate.partner_name or '', job_name)
                            candidate._send_wa(candidate.partner_phone, msg)

                            data[reminder_key + '_at'] = now_utc.isoformat()
                            data['last_activity'] = now_utc.isoformat()
                            candidate._set_wa_data(data)

                            _logger.info("Reminder sent to applicant %d (%s) in %s",
                                         candidate.id, candidate.partner_name, state)

        # ─── Rescue candidates stuck in prescreening_eval > 1h ───
        self._cron_rescue_stuck_eval(excluded_stages)

        # ─── Flowmingo video interview reminders (separate logic using flowmingo_sent_at) ───
        self._cron_flowmingo_video_reminders(now_utc, now_mexico, excluded_stages)

        # ─── NDA signed check for onboarding candidates ───
        self._cron_check_nda_signed(excluded_stages)

        self.env.cr.commit()

    def _cron_rescue_stuck_eval(self, excluded_stages):
        """
        Rescue candidates stuck in prescreening_eval for more than 1 hour.
        This happens when the LLM call fails silently and the state is never resolved.
        Re-runs the evaluation; if LLM still fails, applies fallback (Flowmingo).
        Runs as part of _cron_chatbot_lifecycle.
        """
        from datetime import datetime as dt, timedelta, timezone

        cutoff = fields.Datetime.now() - timedelta(hours=1)
        stuck = self.env["hr.applicant"].sudo().search([
            ("wa_chat_state", "=", "prescreening_eval"),
            ("active", "=", True),
            ("write_date", "<", cutoff),
            ("stage_id", "not in", excluded_stages),
        ])

        if not stuck:
            return

        _logger.info("Rescue: found %d candidates stuck in prescreening_eval", len(stuck))

        for candidate in stuck:
            data = candidate._get_wa_data()
            if data.get("opted_out"):
                continue

            _logger.info(
                "Rescue: retrying eval for applicant %d (%s)",
                candidate.id, candidate.partner_name,
            )
            candidate.with_user(1).message_post(
                body=Markup(
                    '<div style="background:#fff3e0;padding:8px;border-left:4px solid #FF9800;border-radius:6px;">'
                    '<b>🔄 Rescatando candidato atascado en prescreening_eval</b> — reintentando evaluación.</div>'
                ),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            try:
                candidate._evaluate_conversational_screening(data)
            except Exception as exc:
                _logger.error(
                    "Rescue: eval retry failed for applicant %d: %s", candidate.id, exc
                )
                # Last resort: notify Aleix and send Flowmingo
                candidate._notify_aleix(
                    "🆘 *%s* (%s): bloqueado en prescreening_eval, reintento falló: %s. "
                    "Acción manual requerida." % (
                        candidate.partner_name or "?",
                        candidate.job_id.name if candidate.job_id else "?",
                        str(exc)[:100],
                    )
                )

    def _cron_flowmingo_video_reminders(self, now_utc, now_mexico, excluded_stages):
        """
        Handle 24h reminder and 72h deserto for videoentrevista_pendiente candidates.
        Uses flowmingo_sent_at for precise timing (not last_activity which resets on any event).
        """
        from datetime import datetime as dt, timezone

        ICP = self.env["ir.config_parameter"].sudo()

        # Filter videoentrevista_pendiente: only JCF candidates get JCF flow
        candidates = self.env["hr.applicant"].sudo().search([
            ("wa_chat_state", "=", "videoentrevista_pendiente"),
            ("active", "=", True),
            ("stage_id", "not in", excluded_stages),
            ("is_jcf_candidate", "=", True),
        ])

        now_aware = now_utc.replace(tzinfo=timezone.utc) if now_utc.tzinfo is None else now_utc

        for candidate in candidates:
            data = candidate._get_wa_data()

            # Skip opted-out candidates
            if data.get("opted_out"):
                continue

            flowmingo_sent_at = data.get("flowmingo_sent_at")
            if not flowmingo_sent_at:
                # Fallback: use write_date (candidate was set to this state but timestamp missing)
                flowmingo_sent_at = (
                    candidate.write_date.isoformat() if candidate.write_date else None
                )
                if flowmingo_sent_at:
                    data["flowmingo_sent_at"] = flowmingo_sent_at
                    candidate._set_wa_data(data)

            if not flowmingo_sent_at:
                continue

            try:
                sent_dt = dt.fromisoformat(flowmingo_sent_at.replace("Z", "+00:00"))
                if sent_dt.tzinfo is None:
                    sent_dt = sent_dt.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue

            hours_since_sent = (now_aware - sent_dt).total_seconds() / 3600

            candidate_first = (
                (candidate.partner_name or "").split()[0] or "candidato/a"
            )
            job_name = candidate.job_id.name if candidate.job_id else "la vacante"
            flowmingo_url = (
                (candidate.job_id.flowmingo_interview_url if candidate.job_id else "")
                or ICP.get_param("onrentx.flowmingo_interview_url", "")
            )
            url_fragment = ("\n\n👉 %s" % flowmingo_url) if flowmingo_url else ""

            # 72h+ → deserto
            if hours_since_sent >= 72:
                if not data.get("flowmingo_deserto_sent"):
                    _logger.info(
                        "Flowmingo 72h deserto for applicant %d (%s) — %.1fh since sent",
                        candidate.id, candidate.partner_name, hours_since_sent,
                    )
                    # Move to Deserto stage
                    deserto_stage = self.env["hr.recruitment.stage"].sudo().search([
                        ("name", "ilike", "Deserto"),
                    ], limit=1)
                    if deserto_stage:
                        candidate.stage_id = deserto_stage.id

                    candidate.wa_chat_state = "rechazado"
                    data["flowmingo_deserto_sent"] = True
                    candidate._set_wa_data(data)

                    if candidate.partner_phone:
                        candidate._send_wa(
                            candidate.partner_phone,
                            "Hola %s, tu tiempo para grabar la video-entrevista ha expirado 😔\n\n"
                            "Si en el futuro quieres retomar el proceso para *%s*, "
                            "escríbenos y lo vemos con gusto. ¡Mucho éxito! 🙌"
                            % (candidate_first, job_name),
                        )

                    candidate.with_user(1).message_post(
                        body=Markup(
                            '<div style="background:#ffebee;padding:8px;'
                            'border-left:4px solid #c62828;border-radius:6px;">'
                            "<b>⏰ Video-entrevista expirada (72h)</b> — candidato movido a Deserto. "
                            "%.1fh desde que se envió el link Flowmingo.</div>"
                        ) % hours_since_sent,
                        message_type="comment",
                        subtype_xmlid="mail.mt_note",
                    )
                continue

            # 24h+ and not yet reminded → send reminder
            if hours_since_sent >= 24 and not data.get("flowmingo_reminded"):
                _logger.info(
                    "Flowmingo 24h reminder for applicant %d (%s) — %.1fh since sent",
                    candidate.id, candidate.partner_name, hours_since_sent,
                )
                if candidate.partner_phone:
                    candidate._send_wa(
                        candidate.partner_phone,
                        "Hola %s, recuerda que tienes pendiente tu video-entrevista "
                        "para *%s* 🎬\n\n"
                        "¡Es rápida, solo 5 minutos!%s\n\n"
                        "Si tienes problemas técnicos, escribe *ayuda*. 😊"
                        % (candidate_first, job_name, url_fragment),
                    )

                data["flowmingo_reminded"] = True
                candidate._set_wa_data(data)

                candidate.with_user(1).message_post(
                    body=Markup(
                        '<div style="background:#fff3e0;padding:8px;'
                        'border-left:4px solid #ef6c00;border-radius:6px;">'
                        "<b>⏰ Recordatorio 24h enviado</b> — video-entrevista Flowmingo pendiente. "
                        "%.1fh desde que se envió el link.</div>"
                    ) % hours_since_sent,
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )

    def _cron_check_nda_signed(self, excluded_stages):
        """
        Check NDA signature status for all candidates in 'onboarding' state.
        Called from _cron_chatbot_lifecycle every 6 hours.
        Advances candidates to contrato_firmado when sign.oca.request is completed.
        """
        candidates = self.env["hr.applicant"].sudo().search([
            ("wa_chat_state", "=", "onboarding"),
            ("active", "=", True),
            ("stage_id", "not in", excluded_stages),
        ])

        for candidate in candidates:
            data = candidate._get_wa_data()
            if data.get("opted_out"):
                continue
            # Only check if an NDA was actually sent
            if not data.get("nda_sign_request_id"):
                continue
            try:
                candidate._check_nda_signed()
            except Exception as exc:
                _logger.warning(
                    "NDA signed check failed for applicant %d (%s): %s",
                    candidate.id, candidate.partner_name, exc,
                )

    def _get_job_survey(self):
        """Get survey linked to this job position."""
        if self.job_id:
            # Check if job has a survey configured
            try:
                if hasattr(self.job_id, 'survey_id') and self.job_id.survey_id:
                    return self.job_id.survey_id
            except Exception:
                pass
            # Search by name pattern
            survey = self.env["survey.survey"].search([
                ("title", "ilike", self.job_id.name),
            ], limit=1)
            return survey
        return None

    def _send_survey_invite(self, survey):
        """Send survey invitation to candidate."""
        partner = self._get_or_create_partner()
        try:
            invite = self.env["survey.invite"].create({
                "survey_id": survey.id,
                "partner_ids": [(6, 0, [partner.id])],
            })
            invite.action_invite()
            _logger.info("Survey invite sent to %s for %s", partner.name, survey.title)
        except Exception as e:
            _logger.error("Failed to send survey invite: %s", e)
