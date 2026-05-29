# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import hashlib
import hmac
import json
import logging
import difflib
import time
from base64 import b64decode, b64encode

import requests
from markupsafe import Markup

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

OTTER_API_KEY_PARAM = "onrentx.otter_webhook_api_key"
FATHOM_WEBHOOK_SECRET_PARAM = "onrentx.fathom_webhook_secret"
FLOWMINGO_API_KEY_PARAM = "onrentx.flowmingo_webhook_api_key"
FLOWMINGO_WHSEC_PARAM = "onrentx.flowmingo_webhook_secret"  # whsec_xxx from Flowmingo dashboard
ALEIX_WA = "+524424751707"
CAL_BOOKING_URL = "https://cal.com/aleix-onrentx-er3fnp/30min"
FLOWMINGO_PASS_SCORE = 6.0  # Score >= this → send booking link to candidate


class InterviewWebhookController(http.Controller):

    # ─── Fathom.ai webhook (direct, no N8N needed) ───

    def _json_response(self, data, status=200):
        """Return a JSON HTTP response."""
        body = json.dumps(data)
        return request.make_response(
            body,
            headers=[("Content-Type", "application/json")],
            status=status,
        )

    @http.route(
        "/api/recruitment/fathom-webhook",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def receive_fathom_webhook(self, **kwargs):
        """
        Receive interview data from Fathom.ai webhook.

        Fathom sends a POST with JSON body containing:
        - title, share_url, created_at, recording_start_time, recording_end_time
        - calendar_invitees: [{name, email, is_external}]
        - recorded_by: {name, email}
        - default_summary: {content (markdown), template_name}
        - action_items: [{description, assignee, ...}]
        - transcript: [{speaker_name, start_time, end_time, text}]
        """
        raw_body = request.httprequest.data
        headers = request.httprequest.headers

        # Verify webhook signature
        secret = (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param(FATHOM_WEBHOOK_SECRET_PARAM, "")
        )
        # Only verify if Fathom sends signature headers (real webhook)
        has_signature_headers = headers.get("webhook-id") and headers.get("webhook-signature")
        if secret and has_signature_headers:
            if not self._verify_fathom_signature(secret, headers, raw_body):
                _logger.warning("Fathom webhook: invalid signature")
                return self._json_response({"status": "error", "message": "Invalid signature"})

        try:
            data = json.loads(raw_body)
        except (json.JSONDecodeError, TypeError):
            return self._json_response({"status": "error", "message": "Invalid JSON"})
        if not data:
            return self._json_response({"status": "error", "message": "Empty payload"})

        title = data.get("title", "")
        share_url = data.get("share_url", "")
        summary_data = data.get("default_summary") or {}
        summary_content = summary_data.get("content", "")
        action_items = data.get("action_items") or []
        invitees = data.get("calendar_invitees") or []
        recorded_by = data.get("recorded_by") or {}
        recording_start = data.get("recording_start_time", "")
        recording_end = data.get("recording_end_time", "")

        # Filter: only process OnRentX interviews (flexible matching)
        title_lower = title.lower()
        if "onrentx" not in title_lower or ("entrevista" not in title_lower and "jcf" not in title_lower):
            _logger.info("Fathom webhook: skipping non-interview: %s", title)
            return self._json_response({"status": "skipped", "message": "Not an OnRentX interview"})

        # Calculate duration
        duration_min = 0
        if recording_start and recording_end:
            try:
                from datetime import datetime
                fmt = "%Y-%m-%dT%H:%M:%S"
                start = datetime.fromisoformat(recording_start.replace("Z", "+00:00"))
                end = datetime.fromisoformat(recording_end.replace("Z", "+00:00"))
                duration_min = int((end - start).total_seconds() / 60)
            except Exception:
                pass

        # Extract date
        date_str = recording_start[:10] if recording_start else ""

        # Find candidate email (external invitee, not @onrentx.com)
        candidate_email = ""
        candidate_name_from_invitee = ""
        for inv in invitees:
            email = (inv.get("email") or "").strip()
            if email and "@onrentx.com" not in email.lower():
                candidate_email = email
                candidate_name_from_invitee = inv.get("name", "")
                break

        # Extract candidate name from title
        # Formats: "Entrevista OnRentX: Name - Position" or "Entrevistas OnrentX JCF (Name)"
        candidate_name_from_title = ""
        if "(" in title and ")" in title:
            # Format: "Entrevistas OnrentX JCF (Andrea Cruz)"
            candidate_name_from_title = title.split("(")[1].split(")")[0].strip()
        elif ":" in title:
            # Format: "Entrevista OnRentX: Name - Position"
            name_part = title.split(":", 1)[1].strip()
            if " - " in name_part:
                candidate_name_from_title = name_part.split(" - ", 1)[0].strip()
            else:
                candidate_name_from_title = name_part

        # Find applicant
        applicant = self._find_applicant(
            candidate_email,
            candidate_name_from_invitee or candidate_name_from_title,
            title,
        )

        if not applicant:
            _logger.warning(
                "Fathom webhook: no applicant found - email=%s name=%s title=%s",
                candidate_email,
                candidate_name_from_invitee or candidate_name_from_title,
                title,
            )
            return self._json_response({"status": "error", "message": "No matching applicant found"})

        # Deduplication: check if we already posted this Fathom recording
        if share_url:
            existing = request.env["mail.message"].sudo().search([
                ("model", "=", "hr.applicant"),
                ("res_id", "=", applicant.id),
                ("body", "ilike", share_url),
            ], limit=1)
            if existing:
                _logger.info("Fathom duplicate skipped for applicant %d: %s", applicant.id, share_url)
                return self._json_response({"status": "skipped", "message": "Already processed"})

        # Build HTML body
        body_html = (
            '<div style="background:#f0f4ff;padding:12px;'
            'border-left:4px solid #4A90D9;border-radius:8px;">'
            '<b>🎙️ Resumen de Entrevista (Fathom)</b>'
        )
        if date_str:
            body_html += " · %s" % date_str
        if duration_min:
            body_html += " · %s min" % duration_min
        body_html += "<br/><br/>"

        # Summary
        if summary_content:
            # Convert markdown to basic HTML
            summary_html = summary_content.replace("\n\n", "<br/><br/>")
            summary_html = summary_html.replace("\n", "<br/>")
            body_html += "<b>Resumen:</b><br/>%s<br/><br/>" % summary_html

        # Action items
        if action_items:
            body_html += "<b>Action Items:</b><br/>"
            for item in action_items:
                desc = item.get("description", "")
                assignee = item.get("assignee", "")
                if assignee:
                    body_html += "• <b>%s</b>: %s<br/>" % (assignee, desc)
                else:
                    body_html += "• %s<br/>" % desc
            body_html += "<br/>"

        # Link to full transcript
        if share_url:
            body_html += (
                '<a href="%s" target="_blank">'
                "📄 Ver transcripción completa en Fathom</a>" % share_url
            )

        body_html += "</div>"

        self._post_as_admin(
            applicant,
            body=Markup(body_html),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        _logger.info(
            "Fathom summary posted to applicant %d (%s)",
            applicant.id,
            applicant.partner_name,
        )

        # Launch AI evaluation
        _logger.info("Starting AI evaluation for applicant %d...", applicant.id)
        try:
            transcript_text = ""
            if raw_body:
                full_data = json.loads(raw_body) if isinstance(raw_body, (str, bytes)) else data
                transcript_entries = full_data.get("transcript") or []
                for entry in transcript_entries:
                    speaker = entry.get("speaker", {}).get("display_name", "?")
                    text = entry.get("text", "")
                    transcript_text += "%s: %s\n" % (speaker, text)
                _logger.info("Transcript: %d entries, %d chars", len(transcript_entries), len(transcript_text))

            # Commit the summary post first so it's saved even if eval fails
            request.env.cr.commit()

            self._run_ai_evaluation(
                applicant,
                summary_content,
                transcript_text,
                share_url,
                duration_min,
                date_str,
            )
            # Commit evaluation
            request.env.cr.commit()
        except Exception as e:
            _logger.error("AI evaluation failed for applicant %d: %s", applicant.id, e, exc_info=True)

        return self._json_response({
"status": "ok",
"applicant_id": applicant.id,
"applicant_name": applicant.partner_name,
})

    def _run_ai_evaluation(self, applicant, summary, transcript, share_url, duration, date_str):
        """Run AI evaluation of interview using Gemini."""
        import urllib.request

        gemini_key = (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("onrentx.gemini_api_key", "")
        )
        if not gemini_key:
            _logger.warning("No Gemini API key configured")
            return

        # Gather applicant data
        job = applicant.job_id
        job_name = job.name if job else "No especificado"
        job_desc = ""
        if job and job.website_description:
            # Strip HTML tags for the prompt
            import re
            job_desc = re.sub(r'<[^>]+>', '', job.website_description)[:2000]

        candidate_name = applicant.partner_name or "Desconocido"

        # Get CV text from attachments
        cv_text = ""
        attachments = request.env["ir.attachment"].sudo().search([
            ("res_model", "=", "hr.applicant"),
            ("res_id", "=", applicant.id),
            ("mimetype", "=", "application/pdf"),
        ], limit=1)
        if attachments:
            cv_text = "[CV adjunto disponible: %s]" % attachments[0].name
        else:
            cv_text = "[Sin CV adjunto]"

        # Get survey responses via applicant.response_ids (most reliable)
        survey_text = ""
        try:
            response_ids = applicant.response_ids.filtered(lambda r: r.state == "done")
            if response_ids:
                survey_input = response_ids[0]
                survey_text = "Cuestionario: %s\n\n" % (survey_input.survey_id.title or "")
                for line in survey_input.user_input_line_ids:
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
                    elif line.display_name:
                        a = line.display_name
                    if a:
                        survey_text += "P: %s\nR: %s\n\n" % (q, a)
        except Exception as e:
            _logger.warning("Could not read survey for applicant %d: %s", applicant.id, e)

        if not survey_text:
            survey_text = "[Sin cuestionario completado]"

        # Use summary if no transcript
        interview_content = transcript if transcript.strip() else summary

        # Get other candidates for same position (for comparison)
        other_candidates = ""
        if job:
            others = request.env["hr.applicant"].sudo().search([
                ("job_id", "=", job.id),
                ("id", "!=", applicant.id),
                ("active", "=", True),
            ], limit=10)
            if others:
                other_lines = []
                for o in others:
                    stage = o.stage_id.name if o.stage_id else "?"
                    other_lines.append("%s (etapa: %s)" % (o.partner_name, stage))
                other_candidates = "Otros candidatos para este puesto:\n" + "\n".join(other_lines)

        # Build evaluation prompt
        prompt = """Eres el evaluador de reclutamiento de OnRentX, plataforma de renta de maquinaria pesada en México.

CONTEXTO:
- Puesto: %s
- Descripción del puesto: %s
- CV del candidato: %s
- Respuestas del cuestionario: %s
- %s
- Link grabación: %s

TRANSCRIPCIÓN DE LA ENTREVISTA:
%s

INSTRUCCIONES DE EVALUACIÓN:

PASO 1 - EXTRAER REQUISITOS DEL PUESTO: Lee la descripción del puesto arriba y extrae TODOS los requisitos (responsabilidades, requisitos obligatorios, deseables). Cada uno será una fila de la tabla de evaluación.

PASO 2 - EVALUAR CADA REQUISITO (1-5): Para CADA requisito extraído del puesto, busca evidencia en la transcripción Y en el cuestionario. CITA TEXTUALMENTE del transcript entre comillas como evidencia. Si el candidato no mencionó nada sobre un requisito, pon 1/5 y di "No se abordó en la entrevista". Si da respuestas vagas como "sí tengo experiencia" sin detalles, pon 2/5 máximo.

PASO 3 - BANDERAS ROJAS: Detecta inconsistencias entre cuestionario y entrevista (si dijo algo diferente en cada uno). Detecta respuestas vagas/evasivas/genéricas. Problemas JCF (edad >29, no registrado, IMSS activo, ya usó JCF). Expectativa salarial incompatible (JCF paga $9,500). Problemas de ubicación (no en SLP).

PASO 4 - CUESTIONARIO vs ENTREVISTA: Compara lo que dijo en el cuestionario vs lo que dijo en la entrevista. Si hay diferencias, señálalas.

PASO 5 - ACTITUD Y FIT (1-5): ¿Investigó la empresa? ¿Preguntas inteligentes? ¿Interés real? ¿Se adapta a startup?

PASO 6 - JCF: ¿Registrado? ¿Confirmado? ¿Impedimentos?

PASO 7 - COMPARACIÓN: Si hay otros candidatos listados arriba, menciona brevemente cómo se compara este candidato.

FORMATO DE SALIDA (HTML para Odoo, NO markdown):

<h3>🤖 Evaluación AI Post-Entrevista — %s</h3>
<p><b>Candidato:</b> %s | <b>Puesto:</b> %s | <b>Duración:</b> %s min</p>

<h4>Resumen ejecutivo:</h4>
<p>2-3 líneas con lo más importante</p>

<h4>Evaluación por requisitos del puesto:</h4>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%%;">
<tr style="background:#f0f0f0;"><th>Requisito del puesto</th><th>Score</th><th>Evidencia (cita textual de la entrevista)</th><th>Cuestionario dice</th></tr>
<tr><td>Requisito 1 (de la descripción)</td><td>X/5</td><td>"cita textual"</td><td>lo que dijo en survey o N/A</td></tr>
</table>
IMPORTANTE: Incluir UNA FILA POR CADA requisito/responsabilidad listado en la descripción del puesto. No inventar competencias genéricas.

<h4>Banderas rojas:</h4>
<ul><li>bandera con evidencia textual</li></ul>

<h4>Actitud y fit cultural:</h4>
<p>X/5 — evidencia con citas</p>

<h4>JCF:</h4>
<p>estado con detalles</p>

<h4>Comparación con otros candidatos:</h4>
<p>contexto vs otros si aplica</p>

<p style="font-size:18px;"><b>Score post-entrevista: X/5</b></p>
<p style="font-size:16px;"><b>Veredicto: PASA / NO PASA / PASA CON RESERVAS</b></p>
<p><b>Justificación:</b> 1-2 líneas</p>
<p><a href="%s" target="_blank">📹 Ver grabación completa en Fathom</a></p>

REGLAS ESTRICTAS:
- NO evaluar generosamente. Respuestas vagas = score bajo.
- CITAR TEXTUALMENTE del transcript, no parafrasear. Pon las citas entre comillas.
- Si hay inconsistencia CV vs entrevista, bandera roja obligatoria.
- El score refleja REALIDAD, no potencial.
- Cruza las respuestas del cuestionario con lo que dijo en entrevista.
- Responde SOLO con el HTML, sin explicaciones adicionales.""" % (
            job_name, job_desc[:1500], cv_text, survey_text[:1500],
            other_candidates, share_url,
            interview_content[:8000],
            date_str, candidate_name, job_name, duration,
            share_url,
        )

        # Call LiteLLM (Groq Llama 3.3 70B - free, fast)
        litellm_url = "http://159.54.142.132:4000/v1/chat/completions"
        litellm_key = request.env['ir.config_parameter'].sudo().get_param('onrentx.recruitment.litellm_api_key', '')
        payload = json.dumps({
            "model": "groq-llama",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4000,
            "temperature": 0.2,
        })

        max_retries = 3
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(litellm_url, method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("Authorization", "Bearer %s" % litellm_key)
                req.data = payload.encode()
                resp = urllib.request.urlopen(req, timeout=120)
                result = json.loads(resp.read())

                eval_html = result["choices"][0]["message"]["content"]
                # Clean any markdown code fences
                eval_html = eval_html.replace("```html", "").replace("```", "").strip()

                # Post evaluation to applicant chatter
                eval_body = (
                    '<div style="background:#f5f0ff;padding:12px;'
                    'border-left:4px solid #7C3AED;border-radius:8px;">'
                    '%s</div>'
                ) % eval_html

                self._post_as_admin(
                    applicant,
                    body=Markup(eval_body),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
                _logger.info(
                    "AI evaluation posted for applicant %d (%s)",
                    applicant.id, applicant.partner_name,
                )
                return

            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < max_retries - 1:
                    _logger.warning("Gemini rate limit, retry %d/%d", attempt + 1, max_retries)
                    time.sleep(30)
                else:
                    _logger.error("Gemini API error: %s", e)
                    return
            except Exception as e:
                _logger.error("AI evaluation error: %s", e)
                return

    def _verify_fathom_signature(self, secret, headers, raw_body):
        """Verify Fathom webhook signature (HMAC-SHA256)."""
        try:
            webhook_id = headers.get("webhook-id", "")
            webhook_timestamp = headers.get("webhook-timestamp", "")
            webhook_signature = headers.get("webhook-signature", "")

            if not all([webhook_id, webhook_timestamp, webhook_signature]):
                return False

            # Check timestamp (5 min tolerance)
            ts = int(webhook_timestamp)
            if abs(time.time() - ts) > 300:
                return False

            # Construct signed content
            if isinstance(raw_body, bytes):
                body_str = raw_body.decode("utf-8")
            else:
                body_str = raw_body
            signed_content = "%s.%s.%s" % (webhook_id, webhook_timestamp, body_str)

            # Decode secret (remove whsec_ prefix)
            secret_bytes = b64decode(secret.replace("whsec_", ""))

            # Calculate HMAC
            expected_sig = b64encode(
                hmac.new(
                    secret_bytes,
                    signed_content.encode("utf-8"),
                    hashlib.sha256,
                ).digest()
            ).decode("utf-8")

            # Compare against provided signatures
            for sig in webhook_signature.split(" "):
                if "," in sig:
                    sig = sig.split(",", 1)[1]
                if hmac.compare_digest(expected_sig, sig):
                    return True
            return False
        except Exception as e:
            _logger.warning("Fathom signature verification error: %s", e)
            return False

    # ─── Generic interview summary endpoint (Otter/manual) ───

    @http.route(
        "/api/recruitment/interview-summary",
        type="json",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def receive_interview_summary(self, **kwargs):
        """Receive interview summary from Otter.ai via Zapier or manual POST."""
        data = json.loads(request.httprequest.data)

        expected_key = (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param(OTTER_API_KEY_PARAM, "")
        )
        if not expected_key or data.get("api_key") != expected_key:
            return {"status": "error", "message": "Invalid API key"}

        meeting_title = data.get("meeting_title", "")
        mt_lower = meeting_title.lower() if meeting_title else ""
        if mt_lower and "onrentx" not in mt_lower:
            return {"status": "skipped", "message": "Not an OnRentX interview"}

        summary = data.get("summary", "").strip()
        if not summary:
            return {"status": "error", "message": "No summary provided"}

        applicant = self._find_applicant(
            data.get("email", "").strip(),
            data.get("candidate_name", "").strip(),
            meeting_title,
        )
        if not applicant:
            return {"status": "error", "message": "No matching applicant found"}

        date = data.get("date", "")
        duration = data.get("duration_minutes", 0)
        transcript_url = data.get("transcript_url", "")

        body_html = (
            '<div style="background:#f0f4ff;padding:12px;'
            'border-left:4px solid #4A90D9;border-radius:8px;">'
            '<b>🎙️ Resumen de Entrevista</b>'
        )
        if date:
            body_html += " · %s" % date
        if duration:
            body_html += " · %s min" % duration
        body_html += "<br/><br/><b>Puntos clave:</b><br/>"
        body_html += summary.replace("\n", "<br/>")
        body_html += "<br/><br/>"
        if transcript_url:
            body_html += (
                '<a href="%s" target="_blank">'
                '📄 Ver transcripción completa</a>' % transcript_url
            )
        body_html += "</div>"

        self._post_as_admin(
            applicant,
            body=Markup(body_html),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        return {
            "status": "ok",
            "applicant_id": applicant.id,
            "applicant_name": applicant.partner_name,
        }

    # ─── Flowmingo webhook (AI video interview score) ───

    @http.route(
        "/api/recruitment/flowmingo-webhook",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def receive_flowmingo_webhook(self, **kwargs):
        """
        Receive AI interview evaluation from Flowmingo.

        Flowmingo sends event: "interview.evaluation.update"
        Signature: HMAC-SHA256 in X-Flowmingo-Signature header (whsec_ secret)

        Payload can vary — we extract fields defensively from both flat and nested structures.

        On score >= FLOWMINGO_PASS_SCORE:
        - Posts result to Odoo chatter
        - Sends WA with Cal.com booking link to candidate
        - Moves candidate to "Entrevista" stage
        - Notifies Aleix via WA
        """
        raw_body = request.httprequest.data
        try:
            data = json.loads(raw_body)
        except (ValueError, TypeError):
            return self._json_response({"status": "error", "message": "Invalid JSON"}, 400)

        # HMAC-SHA256 signature verification (if secret is configured)
        whsec = (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param(FLOWMINGO_WHSEC_PARAM, "")
        )
        if whsec:
            sig_header = request.httprequest.headers.get("X-Flowmingo-Signature", "")
            if not self._verify_flowmingo_signature(whsec, sig_header, raw_body):
                return self._json_response({"status": "error", "message": "Invalid signature"}, 403)
        else:
            # Fallback: API key in body or Authorization header
            expected_key = (
                request.env["ir.config_parameter"]
                .sudo()
                .get_param(FLOWMINGO_API_KEY_PARAM, "")
            )
            if expected_key:
                provided_key = (
                    data.get("api_key")
                    or request.httprequest.headers.get("Authorization", "").replace("Bearer ", "")
                )
                if provided_key != expected_key:
                    return self._json_response({"status": "error", "message": "Invalid API key"}, 403)

        # Only process evaluation events
        event = data.get("event", "")
        if event and event not in ("interview.evaluation.update", "interview.completed"):
            return self._json_response({"status": "skipped", "event": event})

        # Extract candidate info — handle both flat and nested payload formats
        candidate = data.get("candidate") or {}
        evaluation = data.get("evaluation") or data.get("interview") or {}
        interview_set = data.get("interview_set") or data.get("project") or {}

        candidate_name = (
            candidate.get("name") or data.get("candidate_name") or ""
        ).strip()
        candidate_email = (
            candidate.get("email") or data.get("candidate_email") or ""
        ).strip()
        candidate_phone = (
            candidate.get("phone") or data.get("candidate_phone") or ""
        ).strip()
        score_raw = (
            evaluation.get("score") or data.get("score") or 0
        )
        try:
            score = float(score_raw)
        except (ValueError, TypeError):
            score = 0.0
        interview_url = (
            evaluation.get("submission_url") or evaluation.get("url")
            or data.get("submission_url") or ""
        )
        job_name = (
            interview_set.get("title") or interview_set.get("name")
            or data.get("interview_set_title") or ""
        )

        _logger.info(
            "Flowmingo webhook event=%s: %s <%s> score=%.1f job=%s",
            event, candidate_name, candidate_email, score, job_name,
        )

        if not candidate_name and not candidate_email:
            _logger.warning("Flowmingo webhook: missing candidate identity in payload")
            return self._json_response({"status": "error", "message": "Missing candidate info"})

        # Find applicant in Odoo
        applicant = self._find_applicant(candidate_email, candidate_name, job_name)
        if not applicant:
            _logger.warning(
                "Flowmingo webhook: no applicant found for name=%s email=%s",
                candidate_name, candidate_email,
            )
            return self._json_response({"status": "warning", "message": "No applicant found"})

        # Determine pass/fail
        passed = score >= FLOWMINGO_PASS_SCORE

        # Post result to chatter
        score_color = "#27ae60" if passed else "#e74c3c"
        emoji = "✅" if passed else "❌"
        result_label = "APROBÓ" if passed else "NO APROBÓ"
        body_html = (
            '<div style="background:#f8f9fa;padding:12px;'
            'border-left:4px solid {color};border-radius:8px;">'
            '<b>{emoji} Entrevista Digital Flowmingo — {result}</b><br/><br/>'
            '<b>Score:</b> <span style="color:{color};font-weight:bold;">{score:.1f}/10</span>'
            ' (umbral aprobación: {threshold}/10)<br/>'
            '<b>Puesto:</b> {job}<br/>'
        ).format(
            color=score_color, emoji=emoji, result=result_label,
            score=score, threshold=FLOWMINGO_PASS_SCORE, job=job_name or "N/D",
        )
        if interview_url:
            body_html += '<br/><a href="{}" target="_blank">🎥 Ver entrevista en Flowmingo</a><br/>'.format(interview_url)
        if passed:
            body_html += '<br/>📅 <b>Enlace de agenda enviado al candidato por WhatsApp.</b>'
        body_html += "</div>"

        self._post_as_admin(
            applicant,
            body=Markup(body_html),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # Move to "Entrevista" stage if passed — use _post_as_admin helper's admin_id
        if passed:
            entrevista_stage = (
                request.env["hr.recruitment.stage"]
                .sudo()
                .search([("name", "ilike", "Entrevista")], limit=1)
            )
            if entrevista_stage:
                admin = request.env["res.users"].sudo().search(
                    [("share", "=", False), ("active", "=", True)], order="id asc", limit=1
                )
                applicant.with_user(admin.id if admin else 2).write(
                    {"stage_id": entrevista_stage.id}
                )

        # Get WaSender config — SLP reclutamiento (ID 4, +524443015205), fallback to any
        wa_config = (
            request.env["onrentx.wasender.config"]
            .sudo()
            .search([("phone_number", "ilike", "524443015205")], limit=1)
        ) or (
            request.env["onrentx.wasender.config"]
            .sudo()
            .search([("api_key", "!=", False)], limit=1)
        )

        booking_sent = False
        if passed and wa_config:
            # Send Cal.com booking link to candidate
            phone = candidate_phone or (applicant.partner_phone or "").replace(" ", "")
            if phone:
                if not phone.startswith("+"):
                    if len(phone) == 10:
                        phone = "+52" + phone  # México 10 dígitos
                    else:
                        phone = "+" + phone
                candidate_msg = (
                    "¡Hola {name}! 🎉\n\n"
                    "Completaste tu entrevista digital para el puesto de *{job}* en OnRentX.\n\n"
                    "¡Felicitaciones, has pasado a la siguiente etapa! 🚀\n\n"
                    "Agenda tu entrevista final aquí:\n"
                    "📅 {cal_url}\n\n"
                    "Elige el horario que mejor te funcione. ¡Te esperamos!"
                ).format(
                    name=candidate_name or "candidato/a",
                    job=job_name or "el puesto",
                    cal_url=CAL_BOOKING_URL,
                )
                try:
                    requests.post(
                        "https://wasenderapi.com/api/send-message",
                        json={"to": phone, "text": candidate_msg},
                        headers={
                            "Authorization": "Bearer %s" % wa_config.api_key,
                            "Content-Type": "application/json",
                        },
                        timeout=15,
                    )
                    booking_sent = True
                    _logger.info(
                        "Cal.com booking link sent to %s at %s", candidate_name, phone
                    )
                except Exception as e:
                    _logger.warning("Failed to send booking WA to candidate: %s", e)

        # Notify Aleix
        if wa_config:
            aleix_msg = (
                "{emoji} *Flowmingo — Entrevista Digital*\n\n"
                "Candidato: *{name}*\n"
                "Puesto: {job}\n"
                "Score: *{score:.1f}/10* — {verdict}\n"
                "{cal_note}"
                "👉 Odoo applicant ID: {app_id}"
            ).format(
                emoji=emoji,
                name=candidate_name or "Desconocido",
                job=job_name or "N/D",
                score=score,
                verdict="APROBÓ" if passed else "NO APROBÓ",
                cal_note="📅 Enlace agenda enviado al candidato.\n" if passed else "",
                app_id=applicant.id,
            )
            try:
                requests.post(
                    "https://wasenderapi.com/api/send-message",
                    json={"to": ALEIX_WA, "text": aleix_msg},
                    headers={
                        "Authorization": "Bearer %s" % wa_config.api_key,
                        "Content-Type": "application/json",
                    },
                    timeout=15,
                )
            except Exception as e:
                _logger.warning("Failed to notify Aleix of Flowmingo result: %s", e)

        return self._json_response({
            "status": "ok",
            "applicant_id": applicant.id,
            "passed": passed,
            "score": score,
            "booking_sent": booking_sent,
        })

    def _verify_flowmingo_signature(self, secret, signature_header, raw_body):
        """Verify Flowmingo HMAC-SHA256 webhook signature."""
        try:
            clean_secret = secret.replace("whsec_", "")
            expected = hmac.new(
                clean_secret.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(expected, signature_header)
        except Exception as e:
            _logger.warning("Flowmingo signature verification error: %s", e)
            return False

    # ─── Shared helper ───

    def _post_as_admin(self, applicant, **kwargs):
        """
        Post a chatter message on behalf of the admin user.

        In auth="none" routes, env.user is an empty recordset. Using with_user(1)
        fails because user 1 (OdooBot) may not exist. Using sudo() alone also fails
        because _track_finalize deferred hooks still call self.env.user._is_public().
        Fix: look up the first internal active user and bind the env to that user.
        """
        admin = request.env["res.users"].sudo().search(
            [("share", "=", False), ("active", "=", True)], order="id asc", limit=1
        )
        admin_id = admin.id if admin else 2  # fallback to ID 2 (typical Odoo admin)
        applicant.with_user(admin_id).message_post(**kwargs)

    def _fuzzy_find_applicant_by_name(self, name, threshold=0.70):
        """Find applicant by fuzzy name matching (70%+ similarity)."""
        if not name:
            return None

        from difflib import SequenceMatcher

        normalized_search = name.lower().strip()
        normalized_search = " ".join(normalized_search.split())

        applicants = request.env["hr.applicant"].sudo().search([
            ("partner_name", "!=", False)
        ])

        best_match = None
        best_ratio = 0.0

        for applicant in applicants:
            if not applicant.partner_name:
                continue
            normalized_applicant = applicant.partner_name.lower().strip()
            normalized_applicant = " ".join(normalized_applicant.split())

            ratio = SequenceMatcher(None, normalized_search, normalized_applicant).ratio()

            if ratio >= threshold and ratio > best_ratio:
                best_match = applicant
                best_ratio = ratio

        if best_match:
            _logger.info("Webhook fuzzy match: '%s' ~ '%s' (%.2f%%)",
                        name, best_match.partner_name, best_ratio * 100)

        return best_match

    def _find_applicant(self, email, candidate_name, meeting_title):
        """Find hr.applicant by email, name, or meeting title."""
        Applicant = request.env["hr.applicant"].sudo()
        applicant = None

        # 1. By email (exact)
        if email:
            applicant = Applicant.search(
                [("email_from", "=ilike", email)], limit=1
            )

        # 2. By name (exact)
        if not applicant and candidate_name:
            applicant = Applicant.search(
                [("partner_name", "=ilike", candidate_name)], limit=1
            )

        # 3. By name (partial - each word)
        if not applicant and candidate_name:
            name_parts = candidate_name.strip().split()
            if len(name_parts) >= 2:
                # Search with first + last name
                applicant = Applicant.search([
                    ("partner_name", "ilike", name_parts[0]),
                    ("partner_name", "ilike", name_parts[-1]),
                ], limit=1)
            elif name_parts:
                applicant = Applicant.search(
                    [("partner_name", "ilike", name_parts[0])], limit=1
                )

        # 4. From meeting title "Entrevista OnRentX: Name - Position" or "(Name)"
        if not applicant and meeting_title:
            name_from_title = ""
            if "(" in meeting_title and ")" in meeting_title:
                name_from_title = meeting_title.split("(")[1].split(")")[0].strip()
            elif ":" in meeting_title:
                name_part = meeting_title.split(":", 1)[1].strip()
                if " - " in name_part:
                    name_from_title = name_part.split(" - ", 1)[0].strip()
                else:
                    name_from_title = name_part

            if name_from_title and name_from_title != candidate_name:
                # Try exact first
                applicant = Applicant.search(
                    [("partner_name", "=ilike", name_from_title)], limit=1
                )
                # Then partial
                if not applicant:
                    title_parts = name_from_title.strip().split()
                    if len(title_parts) >= 2:
                        applicant = Applicant.search([
                            ("partner_name", "ilike", title_parts[0]),
                            ("partner_name", "ilike", title_parts[-1]),
                        ], limit=1)

        # 5. Fuzzy matching on candidate_name vs all applicants
        # 5. Fuzzy matching on candidate_name vs all applicants
        if not applicant and candidate_name:
            applicant = self._fuzzy_find_applicant_by_name(candidate_name)
        
        return applicant
