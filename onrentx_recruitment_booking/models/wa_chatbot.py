# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
WhatsApp Chatbot for Recruitment Pre-screening.

State machine:
  idle → contacto_inicial → esperando_jcf → prescreening (q1..q7)
  → prescreening_eval → [pasa: cuestionario_enviado | no_pasa: rechazado]

Triggered from Odoo button "Iniciar Contacto WA".
Responses handled by webhook in interview_webhooks.py.
"""

import json
import difflib
import logging
import difflib
import re
import difflib
import time
import difflib

import requests
import difflib
from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


WA_CHAT_STATES = [
    ("idle", "Sin contactar"),
    ("contacto_inicial", "Contacto inicial enviado"),
    ("prescreening", "Pre-screening en curso"),
    ("prescreening_eval", "Evaluando pre-screening"),
    ("pasa_pendiente_jcf", "Pasa - Pendiente comprobante JCF"),
    ("listo_entrevista", "Listo para entrevista"),
    ("atendido_humano", "Atendido por humano (bot pausado)"),
    ("rechazado", "Rechazado en pre-screening"),
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
        """Get WASender config - prefer San Luis, then Queretaro, skip Leon (logged out).
        
        ISSUE #7 FIX: Single search with domain, sort in Python (not 3 separate searches).
        """
        # ISSUE #7 FIX: Single query instead of 3 separate searches
        configs = self.env["onrentx.wasender.config"].search([
            ("name", "ilike", "San Luis"),
            "|",
            ("name", "ilike", "Queretaro"),
            "|",
            ("name", "ilike", "Leon"),
        ], order="id")  # Ensure consistent ordering
        
        # Priority order: San Luis (id=4) > Queretaro (id=3) > Leon (id=2)
        # Sort in Python by priority
        priority = {"San Luis": 0, "Queretaro": 1, "Leon": 2}
        sorted_configs = sorted(
            [c for c in configs if c.api_key],
            key=lambda c: priority.get(c.name, 99)
        )
        
        if sorted_configs:
            return sorted_configs[0]
        
        # Fallback: any config with api_key
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
                self._notify_aleix_error("WA no se envió a %s (HTTP %d)" % (clean_phone, resp.status_code))

            # Log to chatter
            body = Markup(
                '<div style="background:#dcf8c6;padding:8px 12px;border-radius:8px;'
                'border-left:4px solid #25D366;margin:4px 0;">'
                '<b>🤖 Bot WA enviado</b> (%s) → %s<br/>%s</div>'
            ) % (config.name, clean_phone, message.replace('\n', '<br/>'))
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
        """Send WA using the config saved in wa_chat_data, or fallback."""
        data = self._get_wa_data()
        config_id = data.get("wasender_config_id")
        config = None
        if config_id:
            config = self.env["onrentx.wasender.config"].browse(config_id)
        if not config or not config.exists() or not config.api_key:
            config = self._get_wasender_config()
        return self._send_wa_with_config(phone, message, config)

    def _call_llm(self, prompt, max_tokens=2000):
        """Call LiteLLM (Groq) and return response text."""
        import urllib.request
        try:
            payload = json.dumps({
                "model": self.env['ir.config_parameter'].sudo().get_param('onrentx.recruitment.litellm_model', 'groq-llama'),
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.3,
            })
            req = urllib.request.Request(self.env['ir.config_parameter'].sudo().get_param('onrentx.recruitment.litellm_url', 'http://159.54.142.132:4000/v1/chat/completions'), method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer %s" % self.env['ir.config_parameter'].sudo().get_param('onrentx.recruitment.litellm_api_key', ''))
            req.data = payload.encode()
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            _logger.error("LLM call failed: %s", e)
            self._notify_aleix_error("LLM caído: %s" % str(e)[:100])
            return None

    def _notify_aleix_error(self, error_msg):
        """Send WA to Aleix when something fails."""
        try:
            config = self._get_wasender_config()
            if config:
                job_name = self.job_id.name if self.job_id else "?"
                requests.post(
                    "https://wasenderapi.com/api/send-message",
                    json={"to": "+524424751707", "text":
                        "⚠️ *Error en bot reclutamiento*\n\n"
                        "Candidato: *%s*\n"
                        "Puesto: %s\n"
                        "Error: %s\n\n"
                        "Revisar en Odoo" % (self.partner_name, job_name, error_msg)
                    },
                    headers={
                        "Authorization": "Bearer %s" % config.api_key,
                        "Content-Type": "application/json",
                    },
                    timeout=10,
                )
        except Exception:
            pass

    def _check_stuck_loop(self):
        """Check if bot is stuck in a loop. Auto-fix: apologize, clean dupes, continue."""
        data = self._get_wa_data()
        conversation = data.get("conversation", [])
        if len(conversation) < 4:
            return False

        # Check last 6 messages - if bot messages are similar, we're looping
        bot_msgs = [m["text"][:50] for m in conversation[-6:] if m["role"] == "bot"]
        if len(bot_msgs) >= 2:
            unique = set(bot_msgs)
            if len(unique) <= 1:
                # AUTO-FIX: apologize, clean duplicates, let next question be different
                self._notify_aleix_error(
                    "Bot en BUCLE (auto-corregido) — %s" % self.wa_chat_state
                )
                self.with_user(1).message_post(
                    body=Markup(
                        '<div style="background:#fff3e0;padding:10px;border-left:4px solid #FF9800;border-radius:8px;">'
                        '<b>🔄 Bucle detectado y auto-corregido</b><br/>'
                        'El bot repitió la misma pregunta. Se envió disculpa y se limpió la conversación.</div>'
                    ),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )

                # Send apology
                self._send_wa(
                    self.partner_phone,
                    "Disculpa, tuvimos un detalle técnico. Ya está resuelto. 😊 Sigamos con otra pregunta."
                )

                # Clean duplicate bot messages from conversation
                cleaned = []
                seen_bot = set()
                for msg in conversation:
                    if msg["role"] == "bot":
                        key = msg["text"][:50]
                        if key in seen_bot:
                            continue
                        seen_bot.add(key)
                    cleaned.append(msg)
                data["conversation"] = cleaned
                self._set_wa_data(data)
                self.env.cr.commit()
                self.invalidate_recordset()

                return True
        return False

    # ─── Actions from Odoo UI ───

    def action_start_wa_contact(self):
        """Button: Open wizard to choose sender then start chatbot."""
        self.ensure_one()

        # Skip if human-attended mode\n        if self.wa_chat_state == "atendido_humano":\n            _logger.info("Chatbot skip: applicant %d in human-attended mode", self.id)\n            return

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

        # Skip if human-attended mode\n        if self.wa_chat_state == "atendido_humano":\n            _logger.info("Chatbot skip: applicant %d in human-attended mode", self.id)\n            return

        # Store selected config for this conversation
        data = self._get_wa_data()
        data["wasender_config_id"] = wasender_config.id
        data["conversation"] = []
        data["turn_count"] = 0
        self._set_wa_data(data)

        # Send first message - go straight to screening
        job_name = self.job_id.name if self.job_id else "una vacante"
        candidate_name = self.partner_name or "candidato/a"

        msg = (
            "Hola %s 👋\n\n"
            "Soy el asistente de reclutamiento de *OnRentX*. "
            "Vi tu postulación para *%s*.\n\n"
            "¿Tienes 5 minutos para platicar sobre el puesto?"
        ) % (candidate_name, job_name)

        if self._send_wa_with_config(self.partner_phone, msg, wasender_config):
            self.wa_chat_state = "contacto_inicial"
            # Move pipeline → Pre-screening WA (id=14)
            try:
                self.stage_id = 14
            except Exception:
                pass
        else:
            raise UserError(_("Error enviando WhatsApp. Verifique el número y la configuración de WASender."))

    def action_reset_wa_chatbot(self):
        """Reset chatbot state to allow re-contact."""
        self.ensure_one()

        # Skip if human-attended mode\n        if self.wa_chat_state == "atendido_humano":\n            _logger.info("Chatbot skip: applicant %d in human-attended mode", self.id)\n            return
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

    # ─── Incoming message handler (called from webhook) ───

    def handle_wa_incoming(self, text, message_id=None):
        """Process incoming WA message based on current chat state.
        
        ISSUE #6 FIX: Parse JSON once at start, save once at end.
        All state changes are batched in local `data` dict, single _set_wa_data() call.
        """
        self.ensure_one()

        # Skip if human-attended mode\n        if self.wa_chat_state == "atendido_humano":\n            _logger.info("Chatbot skip: applicant %d in human-attended mode", self.id)\n            return

        # ── ISSUE #6 FIX: Parse JSON ONCE at start ──
        data = self._get_wa_data()
        state_changed = False  # Track if state changes for single commit at end

        # ── Dedup layer 1: skip if message_id already processed ──
        if message_id:
            processed = data.get("processed_msg_ids", [])
            if message_id in processed:
                _logger.info("Chatbot dedup: msg %s already processed for applicant %d", message_id, self.id)
                return
            # Add to processed list (will save at end)
            processed.append(message_id)
            # Keep only last 20 to avoid unbounded growth
            data["processed_msg_ids"] = processed[-20:]

        # ── Loop detection ──
        if self._check_stuck_loop():
            return

        # ── Dedup layer 2: skip terminal states ──
        state = self.wa_chat_state
        if state in ("rechazado", "prescreening_eval"):
            _logger.info("Chatbot skip: applicant %d already in state %s", self.id, state)
            return

        if state == "idle":
            # Candidate in idle responding (e.g. to reminder or welcome WA)
            self._handle_idle_response(text, data)
        elif state == "contacto_inicial":
            # Use LLM to interpret candidate's response and decide next step
            self._handle_initial_response(text, data)
        elif state == "prescreening":
            self._handle_conversational_turn(text, data)
        elif state == "pasa_pendiente_jcf":
            text_lower = text.lower() if text else ""
            is_image = "[imagen enviada]" in text_lower
            waiting_confirm = data.get("jcf_waiting_confirm", False)
            asks_for_link = any(w in text_lower for w in ["link", "agendar", "entrevista", "cita"])
            cant_register = any(w in text_lower for w in ["no me deja", "no puedo", "no me permite", "no permite", "no deja"])

            if is_image:
                # Photo received — ask for confirmation before accepting
                data["jcf_waiting_confirm"] = True
                state_changed = True
                self._send_wa(
                    self.partner_phone,
                    "📸 Recibimos tu foto.\n\n"
                    "⚠️ *Importante:* Necesitamos el comprobante del *registro completado* "
                    "en JCF (fase final), no las fases intermedias.\n\n"
                    "¿Confirmas que esta foto es tu *registro completado* en "
                    "Jóvenes Construyendo el Futuro? (Sí/No)"
                )
            elif waiting_confirm:
                # Waiting for yes/no confirmation on JCF photo
                if any(w in text_lower for w in ["sí", "si", "yes", "correcto", "confirmo", "esa es"]):
                    data["jcf_waiting_confirm"] = False
                    state_changed = True
                    self._send_wa(
                        self.partner_phone,
                        "¡Gracias %s! ✅ Tu comprobante será revisado.\n\n"
                        "Te enviaremos el link para agendar tu entrevista "
                        "por aquí mismo en cuanto lo verifiquemos. 📅"
                        % (self.partner_name or "")
                    )
                elif any(w in text_lower for w in ["no", "aún no", "todavía", "es otra", "fase"]):
                    data["jcf_waiting_confirm"] = False
                    state_changed = True
                    self._send_wa(
                        self.partner_phone,
                        "Entendido. Necesitamos el comprobante de la *fase final* "
                        "del registro en JCF (registro completado).\n\n"
                        "📌 Completa tu registro en: jovenesconstruyendoelfuturo.stps.gob.mx\n\n"
                        "Cuando tengas el comprobante final, envíalo por aquí. 👍"
                    )
                else:
                    self._handle_faq_response(text, data)
            elif cant_register:
                # Can't register — guide them to JCF support
                self._send_wa(
                    self.partner_phone,
                    "Entendemos. Te recomendamos comunicarte directamente "
                    "con Jóvenes Construyendo el Futuro para que te orienten "
                    "sobre tu caso. 📞\n\n"
                    "📌 Registro: jovenesconstruyendoelfuturo.stps.gob.mx/aprendiz\n"
                    "📞 Centro de atención: 079 (disponible 24h, L-D)\n\n"
                    "Cuando logres completar tu registro, envíanos el comprobante "
                    "por aquí y continuamos con tu proceso. 👍"
                )
            elif asks_for_link:
                self._send_wa(
                    self.partner_phone,
                    "Para enviarte el link de la entrevista necesitamos "
                    "primero tu comprobante de registro JCF (fase final). 📋\n\n"
                    "¿Ya completaste tu registro? Envía la foto del comprobante "
                    "y en cuanto lo verifiquemos te mandamos el link. 👍"
                )
            else:
                self._handle_faq_response(text, data)
        elif state in ("listo_entrevista",):
            self._handle_faq_response(text, data)

        # ── ISSUE #6 FIX: Save state ONCE at end ──
        if state_changed or message_id:
            self._set_wa_data(data)
            self.env.cr.commit()
            self.invalidate_recordset()

    def _handle_idle_response(self, text, data):
        """Handle response from candidate in idle state (pre-survey, responding to welcome/reminder)."""
        context = self._build_candidate_context()
        has_survey_done = any(r.state == "done" for r in self.response_ids) if self.response_ids else False

        prompt = """Eres el asistente de reclutamiento de OnRentX por WhatsApp. Este candidato aún NO ha iniciado el pre-screening formal. Está en proceso de completar su cuestionario.

DATOS:
- Candidato: %s
- Puesto: %s
- Cuestionario completado: %s

MENSAJE DEL CANDIDATO: "%s"

INSTRUCCIONES:
- Si pregunta sobre el proceso, puesto, empresa → contesta brevemente (OnRentX renta maquinaria pesada, programa JCF $9,552/mes, San Luis Potosí)
- Si dice que ya completó el cuestionario → "¡Perfecto! Lo revisaremos y te contactaremos pronto para continuar"
- Si pide que le reenvíen el cuestionario → "Te lo reenviamos a tu correo. Revisa también en spam"
- Si pregunta por horario de trabajo → "Eso lo platicamos en la entrevista"
- Si pide hablar con humano → "Te comunico con el equipo" y añade HUMANO al final
- Urgencia: "El proceso cierra el 31 de marzo, completa tu cuestionario lo antes posible"
- Estilo WhatsApp, máximo 3 líneas
- NUNCA inventes información""" % (
            context["candidate_name"],
            context["job_name"],
            "Sí" if has_survey_done else "No — pendiente",
            text,
        )

        response = self._call_llm(prompt, max_tokens=200)
        if response:
            answer = response.strip().strip('"').strip("'")
            needs_human = "HUMANO" in answer
            answer = answer.replace("HUMANO", "").strip()
            self._send_wa(self.partner_phone, answer)

            if needs_human:
                self.with_user(1).message_post(
                    body=Markup(
                        '<div style="background:#ffebee;padding:10px;border-left:4px solid #f44336;border-radius:8px;">'
                        '<b>🚨 Candidato (idle) solicita hablar con humano</b><br/>'
                        'Mensaje: <i>%s</i></div>'
                    ) % text,
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )

            # If survey is done but chatbot hasn't started, it means eval should happen
            if has_survey_done and self.wa_chat_state == "idle":
                _logger.info("Candidate %d has completed survey in idle state, eval should have triggered", self.id)
        else:
            self._send_wa(
                self.partner_phone,
                "Gracias por tu mensaje. Revisa tu correo electrónico "
                "y completa el cuestionario para avanzar. 📋"
            )

    def _handle_initial_response(self, text, data):
        """Handle candidate's first response with LLM intelligence."""
        context = self._build_candidate_context()

        prompt = """Eres el asistente de reclutamiento de OnRentX por WhatsApp. Acabas de contactar a un candidato por primera vez para el puesto de "%s" y te respondió.

CONTEXTO:
- Candidato: %s
- Puesto: %s
- Le enviaste un mensaje presentándote y preguntando si está disponible para platicar 5 min

SU RESPUESTA: "%s"

ANALIZA la respuesta y responde con un JSON:
{"intent": "DISPONIBLE|NO_DISPONIBLE|PREGUNTA|OTRO", "reply": "tu respuesta natural"}

REGLAS:
- DISPONIBLE: si dice sí, ok, claro, va, dale, estoy disponible, etc → reply debe ser: "¡Perfecto! 💪 Te haré unas preguntas sobre tu experiencia. Trata de ser lo más específico posible — con ejemplos concretos, nombres de empresas o proyectos, y resultados. Eso nos ayuda mucho a conocerte mejor. ¡Vamos!"
- NO_DISPONIBLE: si dice no, estoy ocupado, después, mañana, etc → reply amable pero con urgencia: "Entiendo, pero el proceso cierra el 31 de marzo. ¿Podrías en otro momento hoy o mañana?"
- PREGUNTA: si pregunta quién eres, qué empresa, cuánto pagan, de qué se trata, etc → reply contesta brevemente (OnRentX renta maquinaria pesada, programa JCF $9,552/mes, puesto %s) y vuelve a preguntar si puede platicar
- OTRO: cualquier otra cosa → reply interpreta y responde naturalmente
- Si dice "ya hablé con alguien" / "ya envié mis datos" / "ya lo hice" → reply "¡Perfecto! Solo necesitamos completar unas preguntas rápidas por aquí para avanzar en tu proceso."
- Si pide hablar con humano → reply "Te comunico con el equipo, te contactarán pronto" y pon intent HUMANO
- NUNCA inventes información, si no sabes algo di "eso lo platicamos en la entrevista"

Responde SOLO el JSON, nada más.""" % (
            context["job_name"],
            context["candidate_name"],
            context["job_name"],
            text,
            context["job_name"],
        )

        response = self._call_llm(prompt, max_tokens=200)
        intent = "DISPONIBLE"
        reply = "¡Perfecto! 💪 Te haré unas preguntas sobre tu experiencia. Trata de ser lo más específico posible — con ejemplos concretos, nombres de empresas o proyectos, y resultados. ¡Vamos!"

        if response:
            try:
                match = re.search(r'\{.*\}', response, re.DOTALL)
                if match:
                    result = json.loads(match.group())
                    intent = result.get("intent", "DISPONIBLE")
                    reply = result.get("reply", reply)
            except (json.JSONDecodeError, TypeError):
                pass

        self._send_wa(self.partner_phone, reply)

        if intent == "DISPONIBLE":
            # Start screening
            data["conversation"] = []
            data["turn_count"] = 0
            self._set_wa_data(data)
            self.wa_chat_state = "prescreening"
            self.env.cr.commit()
            self.invalidate_recordset()
            try:
                self._send_next_conversational_turn(data)
            except Exception as e:
                _logger.error("Failed to send first question to applicant %d: %s", self.id, e)
        # For NO_DISPONIBLE, PREGUNTA, OTRO → stay in contacto_inicial, wait for next message

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

    def _handle_faq_response(self, text, data):
        """Handle FAQs from candidates using LLM."""
        # Pre-LLM: detect human handoff request
        text_lower = (text or "").lower()
        if any(phrase in text_lower for phrase in [
            "hablar con alguien", "hablar con una persona", "hablar con humano",
            "comunicarme con", "pasar con alguien", "contactar a alguien",
            "quiero hablar con", "necesito hablar con",
        ]):
            self._send_wa(
                self.partner_phone,
                "Te comunico con el equipo de reclutamiento. "
                "Te contactarán pronto por este medio. 📋"
            )
            self.with_user(1).message_post(
                body=Markup(
                    '<div style="background:#ffebee;padding:10px;border-left:4px solid #f44336;border-radius:8px;">'
                    '<b>🚨 Candidato solicita hablar con humano</b><br/>'
                    'Mensaje: <i>%s</i></div>'
                ) % text,
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            if self.user_id:
                try:
                    self.activity_schedule(
                        "mail.mail_activity_data_todo",
                        summary="URGENTE: %s quiere hablar con humano" % self.partner_name,
                        note="Mensaje: %s" % text[:200],
                        user_id=self.user_id.id,
                    )
                except Exception:
                    pass

            # Notify Aleix on personal WA
            try:
                import requests as http_requests
                config = self._get_wasender_config()
                if config:
                    job_name = self.job_id.name if self.job_id else "?"
                    notify_msg = (
                        "🚨 *Candidato pide hablar con humano*\n\n"
                        "Candidato: *%s*\n"
                        "Puesto: %s\n"
                        "Mensaje: _%s_\n\n"
                        "Respóndele desde Odoo → ficha del candidato → botón WA"
                    ) % (self.partner_name, job_name, text[:150])
                    http_requests.post(
                        "https://wasenderapi.com/api/send-message",
                        json={"to": "+524424751707", "text": notify_msg},
                        headers={
                            "Authorization": "Bearer %s" % config.api_key,
                            "Content-Type": "application/json",
                        },
                        timeout=15,
                    )
            except Exception:
                pass
            return

        context = self._build_candidate_context()
        booking_url = self.booking_portal_url if hasattr(self, 'booking_portal_url') else ""
        booking_state = ""
        if self.booking_id:
            booking_state = self.booking_id.state or "pending"
            if self.booking_id.start:
                # Convert UTC to Mexico City time (UTC-6)
                from datetime import timedelta
                local_time = self.booking_id.start - timedelta(hours=6)
                booking_state += " (%s hora México)" % local_time.strftime("%d/%m/%Y %H:%M")

        prompt = """Eres el asistente de reclutamiento de OnRentX por WhatsApp. El candidato ya pasó el pre-screening y está en proceso de agendar su entrevista.

DATOS DEL CANDIDATO:
- Nombre: %s
- Puesto: %s
- Estado: Listo para entrevista
- Booking: %s
- Link de agenda: %s

SOBRE ONRENTX:
- OnRentX es una plataforma tecnológica de renta de maquinaria pesada en México
- Somos una startup en crecimiento, ambiente dinámico y colaborativo
- Operamos en San Luis Potosí (zona Garita de Jalisco) y también en otra ubicación en la ciudad
- El equipo es pequeño pero con visión grande — cada persona tiene impacto directo
- Usamos tecnología moderna (Odoo, apps propias, automatización)

HORARIO DE TRABAJO:
- El horario específico se detalla en la descripción del puesto más abajo
- Si no se menciona horario en la descripción, di: "El horario base es L-V con flexibilidad, los detalles exactos los platicamos en la entrevista"
- OnRentX es flexible con los horarios, nos adaptamos dentro de lo razonable
- Trabajo presencial en San Luis Potosí

SOBRE EL PROCESO Y ENTREVISTA:
- Las entrevistas son DIGITALES por Google Meet (videollamada)
- Horario disponible: Lunes a Viernes 11:30-18:00, Sábados 11:30-14:00
- Duración: ~30 minutos
- Entrevistador: Aleix Llevat (CEO)
- Para la entrevista necesitas: buena conexión a internet, lugar tranquilo, cámara encendida

PROGRAMA JCF (Jóvenes Construyendo el Futuro):
- Programa GRATUITO del Gobierno de México para jóvenes 18-29 años que no estudian ni trabajan
- Beca mensual: $9,582.47 MXN (equivalente a salario mínimo 2026), pagado directamente por el gobierno
- Duración: hasta 12 meses de capacitación
- Incluye seguro médico IMSS durante toda la capacitación
- Al terminar: constancia de capacitación oficial (vale como 1 año de experiencia laboral)
- Posibilidad de ser contratado por el centro de trabajo (OnRentX)
- Requisitos: 18-29 años, no estar estudiando ni trabajando, residir en México
- Registro en: jovenesconstruyendoelfuturo.stps.gob.mx/aprendiz
- Pasos del registro: 1) Crear usuario/contraseña, 2) Completar info y subir documentos, 3) Elegir centro de trabajo (OnRentX), 4) Entrevista con el centro
- No se requiere ningún nivel de estudios para participar
- El registro es gratuito — si alguien pide pago, es fraude (reportar al 079)
- Solo se puede participar UNA vez — si ya participó antes, no puede volver a inscribirse
- Registro presencial disponible en oficinas móviles: jovenesconstruyendoelfuturo.stps.gob.mx/oficinas-moviles/
- Si tienen problemas con el registro → Centro de atención 079 (24h, L-D)
- El programa prioriza municipios con altos niveles de pobreza
- Personas con discapacidad SÍ pueden participar

QUÉ NECESITAS:
- INE vigente
- CURP
- Comprobante de domicilio reciente
- Comprobante de registro JCF
- NSS (Número de Seguridad Social) — si no tienes, se tramita

DESCRIPCIÓN DEL PUESTO:
%s

MENSAJE DEL CANDIDATO: "%s"

FECHA ACTUAL: %s
URGENCIA:
- La fecha límite de decisión es el 31 de marzo de 2026
- El programa inicia el 1 de abril
- NUNCA propongas fechas que ya pasaron — hoy es %s, solo propón fechas de HOY en adelante
- Si el candidato no ha agendado, es urgente: "Te recomendamos agendar esta semana"
- Si dice "la próxima semana" → "Las entrevistas son esta semana antes del 31, ¿tienes algún horario disponible?"
- PROHIBIDO ABSOLUTO: NUNCA confirmes una cita, NUNCA propongas horarios específicos, NUNCA digas "confirmo tu entrevista para X fecha". Tú NO puedes agendar. Solo di "te envío el link para que TÚ elijas el horario que mejor te funcione". Si el candidato propone un horario, di "perfecto, te envío el link ahora para que lo agendes formalmente"
- Si el candidato menciona IMSS activo o baja de IMSS → dile que intente registrarse en jovenesconstruyendoelfuturo.stps.gob.mx, a veces el sistema sí permite el registro. Si no le deja, que nos avise.

INSTRUCCIONES:
- Responde como un asistente de reclutamiento real, amable y profesional
- Usa estilo WhatsApp (corto, directo, con emojis moderados)
- Si el candidato no ha agendado, recuérdale: %s
- Si preguntan algo del puesto, usa la descripción de arriba
- Si preguntan por horario de trabajo, sueldo extra, o info que NO esté aquí → di "Eso lo platicamos en la entrevista 😊"
- Si el candidato pide hablar con una persona → di "Te comunico con el equipo de reclutamiento, te contactarán pronto por este medio" y AÑADE al final de tu respuesta la palabra HUMANO
- NUNCA inventes información que no esté en este prompt
- No repitas "próximamente te enviaremos el link" en cada mensaje, varía tus respuestas
- Máximo 3-4 líneas de respuesta""" % (
            context["candidate_name"],
            context["job_name"],
            booking_state or "Pendiente de agendar",
            booking_url or "No disponible aún",
            context["job_desc"][:1000] or "No disponible",
            text,
            fields.Date.today().strftime("%d/%m/%Y"),
            fields.Date.today().strftime("%d/%m/%Y"),
            booking_url or "próximamente te enviaremos el link",
        )

        response = self._call_llm(prompt, max_tokens=300)
        if response:
            answer = response.strip().strip('"').strip("'")
            # Check if LLM flagged it needs human attention
            needs_human = "HUMANO" in answer or any(w in answer.lower() for w in [
                "equipo de reclutamiento", "te comunicaremos", "le pasaré",
            ])
            # Remove the HUMANO flag before sending
            answer = answer.replace("HUMANO", "").strip()
            self._send_wa(self.partner_phone, answer)
            if needs_human:
                self.with_user(1).message_post(
                    body=Markup(
                        '<div style="background:#fff3e0;padding:8px;border-left:4px solid #FF9800;border-radius:6px;">'
                        '<b>💬 Candidato necesita atención humana</b><br/>'
                        'Mensaje: <i>%s</i><br/>'
                        'Respuesta bot: <i>%s</i></div>'
                    ) % (text, answer[:200]),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
                if self.user_id:
                    try:
                        self.activity_schedule(
                            "mail.mail_activity_data_todo",
                            summary="Candidato %s necesita respuesta humana" % self.partner_name,
                            note="Mensaje WA: %s" % text[:200],
                            user_id=self.user_id.id,
                        )
                    except Exception:
                        pass
        else:
            # LLM failed, send generic response
            self._send_wa(
                self.partner_phone,
                "Gracias por tu mensaje. Lo pasaré al equipo de reclutamiento. "
                "Te responderemos pronto. 📋"
            )

    def _handle_conversational_turn(self, text, data):
        """Handle a conversational turn during pre-screening.
        
        ISSUE #6 FIX: Does NOT call _set_wa_data() for normal flow.
        Parent (handle_wa_incoming) saves once at end.
        Only saves immediately when transitioning to prescreening_eval.
        """
        # Prevent duplicate webhook processing
        if self.wa_chat_state in ('prescreening_eval', 'pasa_pendiente_jcf', 'rechazado', 'listo_entrevista'):
            _logger.info("WA chatbot: Ignorando mensaje duplicado, estado=%s", self.wa_chat_state)
            return

        conversation = data.get("conversation", [])
        turn_count = data.get("turn_count", 0)

        # Save candidate's response (in-memory, parent will persist)
        conversation.append({"role": "candidate", "text": text})
        turn_count += 1
        data["conversation"] = conversation
        data["turn_count"] = turn_count
        # ISSUE #6 FIX: Removed _set_wa_data() here - parent saves at end

        # After 5-7 turns, consider evaluating
        if turn_count >= 5:
            # Hard stop at 7 turns
            if turn_count >= 7:
                self.wa_chat_state = "prescreening_eval"
                # Must save immediately before evaluation
                self._set_wa_data(data)
                self.env.cr.commit()
                self.invalidate_recordset()
                self._evaluate_conversational_screening(data)
                return

            # Ask LLM if we have enough info
            should_continue = self._should_continue_screening(data)
            if not should_continue:
                # Lock state THEN evaluate
                self.wa_chat_state = "prescreening_eval"
                # Must save immediately before evaluation
                self._set_wa_data(data)
                self.env.cr.commit()
                self.invalidate_recordset()
                self._evaluate_conversational_screening(data)
                return

        # Generate next question based on conversation so far
        # Parent will save after this returns
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

        prompt = """Eres un reclutador EXIGENTE de OnRentX haciendo pre-screening por WhatsApp.

PUESTO: %s
DESCRIPCIÓN: %s

CANDIDATO: %s
CV: %s
CUESTIONARIO:
%s

CONVERSACIÓN:
%s

TURNO: %d de 7

REGLAS PARA LA SIGUIENTE PREGUNTA:
1. Si la última respuesta fue VAGA o GENÉRICA → pide ejemplo concreto UNA VEZ. Si ya insististe y el candidato respondió (aunque sea parcialmente), AVANZA al siguiente tema. NUNCA hagas la misma pregunta más de 2 veces.
2. Si la respuesta suena a ChatGPT → pide ejemplo PERSONAL real UNA VEZ, luego avanza.
3. Si el candidato NO respondió la pregunta → repite UNA VEZ. Si sigue sin responder, avanza y anota como bandera roja.
4. CRUZA fuentes UNA VEZ por inconsistencia. Si ya confrontaste y el candidato explicó, acepta y pasa a otro tema.
5. REGLA ANTI-BUCLE: revisa la conversación. Si tu última pregunta es MUY similar a una anterior, CAMBIA DE TEMA obligatoriamente.
5. Turno 4-5: incluye UN CASO PRÁCTICO de OnRentX: "OnRentX renta maquinaria pesada. [situación real del puesto]. ¿Cómo lo resolverías?"
6. Turno 6-7: pregunta sobre JCF y expectativa salarial: "El programa JCF paga $9,582/mes. ¿Estás de acuerdo con ese esquema?"
7. Cada pregunta sobre un tema DISTINTO — no repitas.
8. Estilo WhatsApp: corto, directo, 1-3 líneas.
9. Si es turno 1: pregunta por su experiencia más relevante para el puesto.

Responde SOLO la pregunta, sin explicaciones.""" % (
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
            self.invalidate_recordset()
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
        # FIX: Verificar si ya evaluamos para evitar duplicados
        if self.wa_chat_state in ('pasa_pendiente_jcf', 'rechazado', 'listo_entrevista'):
            _logger.info("WA chatbot: Evaluación ya completada para applicant %d, ignorando", self.id)
            return

        context = self._build_candidate_context()
        conversation = data.get("conversation", [])

        conv_text = "\n".join([
            "%s: %s" % ("Candidato" if m["role"] == "candidate" else "Entrevistador", m["text"])
            for m in conversation
        ])

        prompt = """Evalúa ESTRICTAMENTE la conversación de pre-screening de este candidato para "%s" en OnRentX.

PUESTO: %s
DESCRIPCIÓN: %s
CV: %s
CUESTIONARIO:
%s

CONVERSACIÓN:
%s

EVALÚA CON ESTAS REGLAS ESTRICTAS:

SCORING (1-5 por requisito):
- 1/5: No tiene la habilidad, no dio evidencia
- 2/5: Respuesta vaga/genérica sin ejemplos concretos ("tengo experiencia en...")
- 3/5: Dio UN ejemplo concreto pero sin profundidad
- 4/5: Ejemplos concretos con resultados medibles
- 5/5: Experiencia demostrable, resultados cuantificados, aplica directamente al puesto

BANDERAS ROJAS (busca activamente):
- Respuestas que suenan a ChatGPT (perfectas pero sin detalles personales)
- Inconsistencias entre cuestionario y conversación WA
- No respondió preguntas directas (cambió de tema)
- Nunca dio un ejemplo concreto con nombres/fechas/números
- Dijo "conozco" o "he trabajado con" sin demostrar HOW

VEREDICTO:
- NO_PASA (score < 3): respuestas vagas, sin experiencia real, inconsistencias graves
- CON_RESERVAS (score 3-3.4): algo de experiencia pero dudas importantes
- PASA (score >= 3.5): evidencia real de competencias, ejemplos concretos

IMPORTANTE: NO seas generoso. Si el candidato solo dio respuestas genéricas sin ejemplos concretos, el score NO puede ser mayor a 2.5. Un "tengo experiencia en X" sin detallar cuándo/dónde/cómo vale 2/5.

JSON:
{
  "scores": [{"requirement": "requisito", "score": X, "evidence": "cita textual"}],
  "overall_score": X,
  "red_flags": ["flag"],
  "verdict": "PASA|NO_PASA|CON_RESERVAS",
  "summary": "2 líneas"
}""" % (
            context["job_name"], context["job_name"],
            context["job_desc"][:1000], context["cv"],
            context["survey"][:800], conv_text,
        )

        response = self._call_llm(prompt, max_tokens=1000)
        if not response:
            _logger.error("Pre-screening eval LLM failed for applicant %d", self.id)
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
        ) % (context["job_name"], overall_score, verdict)

        if scores:
            body_html += '<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;width:100%%;">'
            body_html += '<tr style="background:#f0f0f0;"><th>Requisito</th><th>Score</th><th>Evidencia</th></tr>'
            for s in scores:
                body_html += '<tr><td>%s</td><td>%s/5</td><td>%s</td></tr>' % (
                    s.get("requirement", s.get("question", "?")),
                    s.get("score", "?"),
                    s.get("evidence", s.get("reason", ""))
                )
            body_html += '</table>'

        if red_flags:
            body_html += '<h4>Banderas rojas:</h4><ul>'
            for flag in red_flags:
                body_html += '<li>%s</li>' % flag
            body_html += '</ul>'

        if summary:
            body_html += '<p><b>Resumen:</b> %s</p>' % summary

        body_html += '</div>'

        self.with_user(1).message_post(
            body=Markup(body_html),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # Score-based decision (minimum 3/5 to pass)
        MIN_SCORE = 3.0
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
                ("wa_chat_state", "in", ["pasa_pendiente_jcf", "listo_entrevista"]),
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
            # Move pipeline → No pasó pre-screening (id=17)
            try:
                self.stage_id = 17
            except Exception:
                pass
            self._send_wa(
                self.partner_phone,
                "Hola %s, agradecemos mucho tu interés en OnRentX.\n\n"
                "Después de revisar tu perfil, en esta ocasión no continuaremos "
                "con el proceso para este puesto.\n\n"
                "Te deseamos mucho éxito en tu búsqueda. 🙏" % (self.partner_name or "")
            )
        else:
            # PASA (score >= 3) → pedir comprobante JCF
            self.wa_chat_state = "pasa_pendiente_jcf"
            # Move pipeline stage to Registro JCF (id=2)
            try:
                self.stage_id = 2
            except Exception:
                pass
            self._send_wa(
                self.partner_phone,
                "¡Muy bien %s! 🎉 Has pasado el pre-screening.\n\n"
                "El siguiente paso es agendar tu entrevista presencial. "
                "Para eso necesitamos que nos envíes tu *comprobante de registro* "
                "en Jóvenes Construyendo el Futuro.\n\n"
                "📌 Si aún no te has registrado: jovenesconstruyendoelfuturo.stps.gob.mx\n\n"
                "Cuando lo tengas, envíanos una captura de pantalla o foto por aquí. 📸"
                % (self.partner_name or "")
            )

        # Commit final state (pasa_pendiente_jcf or rechazado) immediately
        self.env.cr.commit()
        self.invalidate_recordset()

        _logger.info(
            "Pre-screening eval for %d (%s): %s (score=%s)",
            self.id, self.partner_name, verdict, overall_score,
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

    def _fuzzy_find_applicant_by_name(self, name, threshold=0.70):
        """Find applicant by fuzzy name matching (70%+ similarity).
        
        Args:
            name: Name to search for
            threshold: Minimum similarity ratio (default 0.70)
        
        Returns:
            hr.applicant record if match found, None otherwise
        """
        if not name:
            return None

        normalized_search = name.lower().strip()
        # Remove extra spaces
        normalized_search = " ".join(normalized_search.split())
        
        applicants = self.env["hr.applicant"].sudo().search([
            ("partner_name", "!=", False)
        ])

        best_match = None
        best_ratio = 0.0

        for applicant in applicants:
            if not applicant.partner_name:
                continue
            normalized_applicant = applicant.partner_name.lower().strip()
            normalized_applicant = " ".join(normalized_applicant.split())
            
            ratio = difflib.SequenceMatcher(None, normalized_search, normalized_applicant).ratio()

            if ratio >= threshold and ratio > best_ratio:
                best_match = applicant
                best_ratio = ratio

        if best_match:
            _logger.info("Fuzzy match found: \047%s\047 ~ \047%s\047 (%.2f%%)", 
                        name, best_match.partner_name, best_ratio * 100)
        
        return best_match


    def action_pause_wa_chatbot(self):
        """Pause chatbot and mark as human-attended.

        The chatbot will stop responding to messages until resumed.
        """
        for applicant in self:
            if applicant.wa_chat_state not in ("idle", "rechazado"):
                applicant.wa_chat_state = "atendido_humano"
                _logger.info("Chatbot paused for applicant %d (%s)", applicant.id, applicant.partner_name)

    def action_resume_wa_chatbot(self):
        """Resume chatbot (return to idle state).

        The chatbot will start responding to messages again.
        """
        for applicant in self:
            if applicant.wa_chat_state == "atendido_humano":
                applicant.wa_chat_state = "idle"
                _logger.info("Chatbot resumed for applicant %d (%s)", applicant.id, applicant.partner_name)
