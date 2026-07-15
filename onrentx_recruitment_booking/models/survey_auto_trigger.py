# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
Auto-trigger pipeline when candidate completes survey.

Survey done → AI evaluates responses → auto-route:
  score >= 3 → start WA chatbot pre-screening automatically
  score < 3  → reject candidate with professional WA message

Phase 10: Added _auto_route_by_score() for zero-intervention routing.
"""

import json
import logging
import re

import requests
from markupsafe import Markup

from odoo import api, models

_logger = logging.getLogger(__name__)

# LiteLLM URL loaded from ir.config_parameter at runtime (see _call_litellm)
# LiteLLM key loaded from ir.config_parameter at runtime (see _call_litellm)
ALEIX_WA = "+524424751707"


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    def write(self, vals):
        """Detect when survey is completed and trigger pipeline."""
        was_not_done = {r.id: r.state != "done" for r in self}
        result = super().write(vals)

        if "state" in vals and vals["state"] == "done":
            for record in self:
                if was_not_done.get(record.id):
                    try:
                        record._on_survey_completed()
                    except Exception as e:
                        _logger.error(
                            "Error processing survey completion for input %d: %s",
                            record.id, e,
                        )
        return result

    def _on_survey_completed(self):
        """Called when a survey is marked as done."""
        self.ensure_one()

        applicant = self._find_applicant()
        if not applicant:
            _logger.info(
                "Survey %d completed but no applicant found (partner=%s)",
                self.id, self.partner_id.name if self.partner_id else "none",
            )
            return

        survey_name = self.survey_id.title or "Cuestionario"
        _logger.info(
            "Survey '%s' completed by applicant %d (%s)",
            survey_name, applicant.id, applicant.partner_name,
        )

        # 1. Post notification in chatter
        body = Markup(
            '<div style="background:#e8f5e9;padding:10px;'
            'border-left:4px solid #4CAF50;border-radius:8px;">'
            '<b>✅ Cuestionario completado</b><br/>'
            'El candidato completó el cuestionario: <b>%s</b>'
            '</div>'
        ) % survey_name

        applicant.with_user(1).message_post(
            body=body,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # 2. AI evaluate survey responses + auto-route
        self._evaluate_and_notify(applicant)

    def _evaluate_and_notify(self, applicant):
        """Evaluate survey responses with AI and auto-route by score."""
        # Build survey responses text
        survey_text = ""
        for line in self.user_input_line_ids:
            q = line.question_id.title if line.question_id else "?"
            a = ""
            if line.suggested_answer_id:
                a = line.suggested_answer_id.value
            elif line.value_text_box:
                a = line.value_text_box
            elif line.value_char_box:
                a = line.value_char_box
            elif line.value_numerical_box:
                a = str(line.value_numerical_box)
            if a:
                survey_text += "P: %s\nR: %s\n\n" % (q, a)

        if not survey_text:
            survey_text = "[Sin respuestas]"

        job_name = applicant.job_id.name if applicant.job_id else "No especificado"
        job_desc = ""
        if applicant.job_id and applicant.job_id.website_description:
            job_desc = re.sub(r'<[^>]+>', '', applicant.job_id.website_description)[:1000]

        # Call LLM for quick evaluation
        prompt = """Evalúa rápidamente las respuestas del cuestionario de este candidato para el puesto de "%s" en OnRentX (programa JCF).

REQUISITOS JCF:
- Edad: 18-29 años
- No estar estudiando ni trabajando formalmente
- No haber participado antes en JCF
- No tener IMSS activo

PUESTO: %s
DESCRIPCIÓN: %s

RESPUESTAS DEL CUESTIONARIO:
%s

Responde en JSON:
{
  "cumple_jcf": true/false,
  "edad": "X años o desconocida",
  "red_flags": ["flag1"],
  "score": X (1-5),
  "resumen": "2 líneas máximo con lo más relevante",
  "recomendacion": "INICIAR_SCREENING / REVISAR / DESCARTAR"
}""" % (job_name, job_name, job_desc[:500], survey_text[:2000])

        import urllib.request
        try:
            payload = json.dumps({
                "model": "groq-llama",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.2,
            })
            req = urllib.request.Request(self.env["ir.config_parameter"].sudo().get_param("onrentx.recruitment.litellm_url", "http://159.54.142.132:4000/v1/chat/completions"), method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer %s" % self.env["ir.config_parameter"].sudo().get_param("onrentx.recruitment.litellm_api_key", ""))
            req.data = payload.encode()
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read())
            response_text = result["choices"][0]["message"]["content"]
        except Exception as e:
            _logger.error("Survey eval LLM failed for applicant %d: %s", applicant.id, e)
            return

        # Parse response
        try:
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            eval_data = json.loads(match.group()) if match else {}
        except (json.JSONDecodeError, TypeError):
            eval_data = {}

        cumple_jcf = eval_data.get("cumple_jcf", "?")
        edad = eval_data.get("edad", "?")
        score = eval_data.get("score", "?")
        resumen = eval_data.get("resumen", "Sin resumen")
        recomendacion = eval_data.get("recomendacion", "REVISAR")
        red_flags = eval_data.get("red_flags", [])

        # Post evaluation to chatter
        flags_html = ""
        if red_flags:
            flags_html = "<br/><b>⚠️ Flags:</b> " + ", ".join(red_flags)

        eval_body = Markup(
            '<div style="background:#e3f2fd;padding:10px;'
            'border-left:4px solid #2196F3;border-radius:8px;">'
            '<b>🤖 Evaluación AI del Cuestionario</b><br/>'
            '<b>Puesto:</b> %s | <b>Score:</b> %s/5<br/>'
            '<b>Cumple JCF:</b> %s | <b>Edad:</b> %s<br/>'
            '<b>Recomendación:</b> %s<br/>'
            '<b>Resumen:</b> %s%s'
            '</div>'
        ) % (job_name, score, cumple_jcf, edad, recomendacion, resumen, flags_html)

        applicant.with_user(1).message_post(
            body=eval_body,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # Parse score number for routing
        try:
            score_num = float(score)
        except (ValueError, TypeError):
            score_num = 0

        # Notify Aleix via WA (for visibility — no action required from him)
        self._notify_aleix_with_score(applicant, score_num, resumen, recomendacion, red_flags)

        # AUTO-ROUTE based on score
        self._auto_route_by_score(applicant, score_num, resumen)

    def _auto_route_by_score(self, applicant, score_num, resumen):
        """
        Auto-route candidate based on survey score.
        score >= 3 → auto-start WA chatbot pre-screening
        score < 3  → reject with professional WA + move to Rechazado stage
        """
        PASS_THRESHOLD = 3.0

        if score_num >= PASS_THRESHOLD:
            # ── PASS: Auto-start WA chatbot pre-screening ──
            _logger.info(
                "Auto-routing applicant %d (%s): score=%.1f >= %.1f → starting chatbot",
                applicant.id, applicant.partner_name, score_num, PASS_THRESHOLD,
            )

            # Get San Luis WaSender config (config_id=4 explicitly)
            wasender_config = applicant.env["onrentx.wasender.config"].sudo().browse(4)
            if not wasender_config.exists() or not wasender_config.api_key:
                wasender_config = applicant._get_wasender_config()

            # Move to Pre-screening stage if it exists
            prescreening_stage = applicant.env["hr.recruitment.stage"].sudo().search([
                "|",
                ("name", "ilike", "Pre-screening"),
                ("name", "ilike", "Preseleccion"),
            ], limit=1)
            if prescreening_stage:
                applicant.stage_id = prescreening_stage.id

            # Start WA chatbot
            try:
                applicant._start_wa_chatbot(wasender_config)
                _logger.info(
                    "Chatbot started for applicant %d (%s)", applicant.id, applicant.partner_name
                )
            except Exception as e:
                _logger.error(
                    "Failed to start chatbot for applicant %d: %s", applicant.id, e
                )

            # Post success in chatter
            applicant.with_user(1).message_post(
                body=Markup(
                    '<div style="background:#e8f5e9;padding:8px;'
                    'border-left:4px solid #4CAF50;border-radius:6px;">'
                    '<b>✅ Auto-routing:</b> Score %.1f/5 → Pre-screening WA iniciado automáticamente.<br/>'
                    'Resumen: %s</div>'
                ) % (score_num, resumen),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )

        else:
            # ── FAIL: Auto-reject candidate ──
            _logger.info(
                "Auto-routing applicant %d (%s): score=%.1f < %.1f → rejecting",
                applicant.id, applicant.partner_name, score_num, PASS_THRESHOLD,
            )

            # Move to Rechazado stage
            rechazado_stage = applicant.env["hr.recruitment.stage"].sudo().search([
                "|",
                ("name", "ilike", "Rechaz"),
                ("name", "ilike", "No pas"),
            ], limit=1)
            if rechazado_stage:
                applicant.stage_id = rechazado_stage.id

            # Set WA state and send rejection
            applicant.wa_chat_state = "rechazado"
            try:
                applicant._send_wa_rejection(score_num)
            except Exception as e:
                _logger.error(
                    "Failed to send rejection WA for applicant %d: %s", applicant.id, e
                )

            # Post rejection in chatter
            applicant.with_user(1).message_post(
                body=Markup(
                    '<div style="background:#fff3e0;padding:8px;'
                    'border-left:4px solid #FF9800;border-radius:6px;">'
                    '<b>⚠️ Auto-routing:</b> Score %.1f/5 → Rechazado automáticamente.<br/>'
                    'Resumen: %s<br/>'
                    'WA de rechazo enviado al candidato.</div>'
                ) % (score_num, resumen),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )

    def _notify_aleix_with_score(self, applicant, score_num, resumen, recomendacion, red_flags=None):
        """Send score notification to Aleix via WA (informational only)."""
        wa_config = self.env["onrentx.wasender.config"].sudo().search([
            ("api_key", "!=", False),
        ], limit=1)

        if not wa_config:
            return

        emoji = "✅" if score_num >= 3 else "❌"
        action_text = "Auto-iniciando pre-screening WA" if score_num >= 3 else "Rechazando automáticamente"
        flags_text = ""
        if red_flags:
            flags_text = "\n⚠️ " + ", ".join(red_flags)

        job_name = applicant.job_id.name if applicant.job_id else "No especificado"
        notify_msg = (
            "%s *Cuestionario evaluado*\n\n"
            "Candidato: *%s*\n"
            "Puesto: %s\n"
            "Score: %.1f/5\n"
            "%s\n"
            "%s\n\n"
            "Acción: %s"
        ) % (
            emoji,
            applicant.partner_name,
            job_name,
            score_num,
            resumen,
            flags_text,
            action_text,
        )

        try:
            requests.post(
                "https://wasenderapi.com/api/send-message",
                json={"to": ALEIX_WA, "text": notify_msg},
                headers={
                    "Authorization": "Bearer %s" % wa_config.api_key,
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            _logger.info("Score notification sent to Aleix for applicant %d", applicant.id)
        except Exception as e:
            _logger.warning("Failed to notify Aleix: %s", e)

    def _find_applicant(self):
        """Find hr.applicant linked to this survey response."""
        Applicant = self.env["hr.applicant"].sudo()

        # 1. Via response_ids (most reliable)
        applicant = Applicant.search([
            ("response_ids", "in", [self.id]),
        ], limit=1)
        if applicant:
            return applicant

        # 2. Via partner_id
        if self.partner_id:
            applicant = Applicant.search([
                ("partner_id", "=", self.partner_id.id),
            ], limit=1)
            if applicant:
                return applicant

        # 3. Via email
        if self.partner_id and self.partner_id.email:
            applicant = Applicant.search([
                ("email_from", "=ilike", self.partner_id.email),
            ], limit=1)
            if applicant:
                return applicant

        # 4. Via fuzzy name matching (fallback)
        if self.partner_id and self.partner_id.name:
            applicant = self._fuzzy_find_applicant_by_name(self.partner_id.name)
            if applicant:
                return applicant

        return None

    def _fuzzy_find_applicant_by_name(self, name):
        """Fuzzy name match for applicant lookup."""
        import difflib
        all_applicants = self.env["hr.applicant"].sudo().search([
            ("partner_name", "!=", False),
        ], limit=200)
        if not all_applicants:
            return None
        names = [a.partner_name for a in all_applicants]
        matches = difflib.get_close_matches(name, names, n=1, cutoff=0.85)
        if matches:
            for a in all_applicants:
                if a.partner_name == matches[0]:
                    return a
        return None
