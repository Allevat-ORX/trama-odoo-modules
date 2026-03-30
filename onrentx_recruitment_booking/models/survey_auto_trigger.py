# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
Auto-trigger pipeline when candidate completes survey.

Survey done → AI evaluates responses → notify Aleix WA with summary.
Aleix decides manually whether to start WA chatbot.
"""

import json
import difflib
import logging
import difflib
import re
import difflib

import requests
import difflib
from markupsafe import Markup

from odoo import api, models

_logger = logging.getLogger(__name__)

LITELLM_URL = "http://159.54.142.132:4000/v1/chat/completions"
LITELLM_KEY = "sk-orx-kAErmWcz1m0tGrS7IsQ4BmALzaAzeeXo"
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

        # 2. AI evaluate survey responses + notify Aleix
        self._evaluate_and_notify(applicant)

        # 3. Create activity for recruiter
        if applicant.user_id:
            try:
                applicant.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary="Cuestionario completado — revisar %s" % applicant.partner_name,
                    note="Revisar evaluación AI del cuestionario. Si cumple requisitos, iniciar pre-screening WA.",
                    user_id=applicant.user_id.id,
                )
            except Exception:
                pass

    def _evaluate_and_notify(self, applicant):
        """Evaluate survey responses with AI and notify Aleix via WA."""
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
            req = urllib.request.Request(LITELLM_URL, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer %s" % LITELLM_KEY)
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

        # Notify Aleix via WA
        emoji = "✅" if recomendacion == "INICIAR_SCREENING" else "⚠️" if recomendacion == "REVISAR" else "❌"
        wa_config = self.env["onrentx.wasender.config"].sudo().search([
            ("api_key", "!=", False),
        ], limit=1)

        if wa_config:
            flags_text = ""
            if red_flags:
                flags_text = "\n⚠️ " + ", ".join(red_flags)

            notify_msg = (
                "%s *Cuestionario completado*\n\n"
                "Candidato: *%s*\n"
                "Puesto: %s\n"
                "Score: %s/5 | Edad: %s\n"
                "JCF: %s\n"
                "%s\n"
                "%s\n\n"
                "Recomendación: *%s*\n"
                "👉 Iniciar chatbot WA desde Odoo si procede"
            ) % (
                emoji,
                applicant.partner_name,
                job_name,
                score, edad,
                "Cumple" if cumple_jcf else "No cumple / Revisar",
                resumen,
                flags_text,
                recomendacion,
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
                _logger.info("Survey eval notification sent to Aleix WA for applicant %d", applicant.id)
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
        if not applicant and self.partner_id and self.partner_id.name:
            applicant = self._fuzzy_find_applicant_by_name(self.partner_id.name)
            if applicant:
                return applicant

        return None
