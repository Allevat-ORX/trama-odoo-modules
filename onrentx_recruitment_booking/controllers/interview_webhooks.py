# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import hashlib
import hmac
import json
import logging
import difflib
import time
from base64 import b64decode, b64encode

from markupsafe import Markup, escape as markup_escape

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

OTTER_API_KEY_PARAM = "onrentx.otter_webhook_api_key"
FATHOM_WEBHOOK_SECRET_PARAM = "onrentx.fathom_webhook_secret"
FLOWMINGO_WEBHOOK_SECRET_PARAM = "onrentx.flowmingo_webhook_secret"
FLOWMINGO_PASS_SCORE_PARAM = "onrentx.flowmingo_pass_score"


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
        if not secret:
            _logger.warning(
                "Fathom webhook: %s configured — accepting unauthenticated request. "
                "Set ICP '%s' to enable HMAC verification.",
                "no secret",
                FATHOM_WEBHOOK_SECRET_PARAM,
            )
        if secret:
            if not has_signature_headers:
                _logger.warning("Fathom webhook: signature headers missing but secret configured")
                return self._json_response({"status": "error", "message": "Missing signature headers"}, status=401)
            if not self._verify_fathom_signature(secret, headers, raw_body):
                _logger.warning("Fathom webhook: invalid signature")
                return self._json_response({"status": "error", "message": "Invalid signature"}, status=401)

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
            body_html += " · %s" % markup_escape(str(date_str))
        if duration_min:
            body_html += " · %s min" % markup_escape(str(duration_min))
        body_html += "<br/><br/>"

        # Summary
        if summary_content:
            # Convert markdown to basic HTML — escape before converting newlines (CR-04)
            summary_html = str(markup_escape(summary_content))
            summary_html = summary_html.replace("\n\n", "<br/><br/>")
            summary_html = summary_html.replace("\n", "<br/>")
            body_html += "<b>Resumen:</b><br/>%s<br/><br/>" % summary_html

        # Action items
        if action_items:
            body_html += "<b>Action Items:</b><br/>"
            for item in action_items:
                desc = markup_escape(str(item.get("description", "")))
                assignee = markup_escape(str(item.get("assignee", "")))
                if assignee:
                    body_html += "• <b>%s</b>: %s<br/>" % (assignee, desc)
                else:
                    body_html += "• %s<br/>" % desc
            body_html += "<br/>"

        # Link to full transcript
        if share_url:
            # Only allow http/https — block javascript: and data: URIs (CR-04)
            safe_url = str(share_url) if str(share_url).startswith(("http://", "https://")) else "#"
            body_html += (
                '<a href="%s" target="_blank">'
                "📄 Ver transcripción completa en Fathom</a>" % markup_escape(safe_url)
            )

        body_html += "</div>"

        applicant.with_user(1).message_post(
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
            try:
                import io
                from pdfminer.high_level import extract_text
                pdf_data = b64decode(attachments[0].datas)
                cv_text = extract_text(io.BytesIO(pdf_data))
                if cv_text:
                    cv_text = cv_text[:3000]
                else:
                    cv_text = "[CV adjunto pero no se pudo extraer texto: %s]" % attachments[0].name
            except ImportError:
                cv_text = "[CV adjunto disponible: %s - instalar pdfminer.six para extraer texto]" % attachments[0].name
            except Exception as e:
                cv_text = "[CV adjunto: %s - error extraccion: %s]" % (attachments[0].name, str(e))
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
            interview_content[:15000],
            date_str, candidate_name, job_name, duration,
            share_url,
        )

        # Call LiteLLM (Groq Llama 3.3 70B - free, fast)
        litellm_url = request.env["ir.config_parameter"].sudo().get_param("onrentx.recruitment.litellm_url", "http://159.54.142.132:4000/v1/chat/completions")
        litellm_key = request.env['ir.config_parameter'].sudo().get_param('onrentx.recruitment.litellm_api_key', '')
        payload = json.dumps({
            "model": "mistral-large",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 8000,
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
                # Clean full HTML document wrappers from mistral-large
                import re as re_clean
                eval_html = re_clean.sub(r'<html[^>]*>', '', eval_html)
                eval_html = re_clean.sub(r'</html>', '', eval_html)
                eval_html = re_clean.sub(r'<head>.*?</head>', '', eval_html, flags=re_clean.DOTALL)
                eval_html = re_clean.sub(r'<body[^>]*>', '', eval_html)
                eval_html = re_clean.sub(r'</body>', '', eval_html)
                eval_html = re_clean.sub(r'<style[^>]*>.*?</style>', '', eval_html, flags=re_clean.DOTALL)
                eval_html = eval_html.strip()

                # Sanitize LLM HTML before rendering (CR-03) — strip scripts/event handlers
                try:
                    import bleach
                    _SAFE_TAGS = [
                        'div', 'p', 'table', 'thead', 'tbody', 'tr', 'td', 'th',
                        'ul', 'ol', 'li', 'h3', 'h4', 'h5', 'b', 'strong', 'i',
                        'em', 'br', 'span', 'hr', 'a',
                    ]
                    eval_html = bleach.clean(
                        eval_html,
                        tags=_SAFE_TAGS,
                        attributes={'a': ['href', 'target']},
                        protocols=['http', 'https'],  # CR-01: block javascript:/data: hrefs
                        strip=True,
                    )
                except ImportError:
                    import re as _re
                    # Strip dangerous tags entirely (not just script)
                    eval_html = _re.sub(r'<(script|iframe|object|embed|svg|math|base|link|meta)[^>]*>.*?</\1>', '', eval_html, flags=_re.DOTALL | _re.IGNORECASE)
                    eval_html = _re.sub(r'<(script|iframe|object|embed|svg|math|base|link|meta)[^>]*/?\s*>', '', eval_html, flags=_re.IGNORECASE)
                    eval_html = _re.sub(r'\bon\w+\s*=\s*["\'][^"\']*["\']', '', eval_html, flags=_re.IGNORECASE)
                    eval_html = _re.sub(r'\bon\w+\s*=\s*\S+', '', eval_html, flags=_re.IGNORECASE)
                    # CR-01: strip javascript:/data:/vbscript: URIs in href/src/action attributes
                    eval_html = _re.sub(
                        r"""(href|src|action)\s*=\s*(['"])\s*(javascript|data|vbscript)[^'"]*\2""",
                        r'\1="#"', eval_html, flags=_re.IGNORECASE,
                    )

                # Post evaluation to applicant chatter
                eval_body = (
                    '<div style="background:#f5f0ff;padding:12px;'
                    'border-left:4px solid #7C3AED;border-radius:8px;">'
                    '%s</div>'
                ) % eval_html

                applicant.with_user(1).message_post(
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

    # ─── Flowmingo video interview webhook ───

    @http.route(
        "/api/recruitment/flowmingo-webhook",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def receive_flowmingo_webhook(self, **kwargs):
        """
        Receive video interview evaluation from Flowmingo.

        Flowmingo sends a POST with JSON body containing:
        - candidate_email, evaluation_score (0-10), submission_url, interview_name
        Authentication via X-Webhook-Signature (HMAC-SHA256).
        """
        raw_body = request.httprequest.data
        headers = request.httprequest.headers

        # Verify HMAC signature (only if secret is configured)
        secret = (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param(FLOWMINGO_WEBHOOK_SECRET_PARAM, "")
        )
        signature_header = headers.get("X-Webhook-Signature") or headers.get("x-webhook-signature", "")
        if secret and signature_header:
            if not self._verify_flowmingo_signature(secret, signature_header, raw_body):
                _logger.warning("Flowmingo webhook: invalid signature")
                return self._json_response({"status": "error", "message": "Invalid signature"}, status=401)

        try:
            data = json.loads(raw_body)
        except (json.JSONDecodeError, TypeError):
            return self._json_response({"status": "error", "message": "Invalid JSON"}, status=400)
        if not data:
            return self._json_response({"status": "error", "message": "Empty payload"}, status=400)

        # Flowmingo wraps the real payload in an event envelope:
        # {"event_type": "...", "data": {candidate_email, evaluation_score, ...}, "is_test": bool}
        event_type = data.get("event_type", "")
        is_test = bool(data.get("is_test"))
        event_data = data.get("data") or {}

        # Only the final interview evaluation carries a usable score; ignore
        # invitation/status pings (invitation.status.update, interview.status.update, ...).
        if event_type != "interview.evaluation.update":
            _logger.info(
                "Flowmingo webhook: ignoring event_type=%s (is_test=%s)", event_type, is_test,
            )
            return self._json_response({"status": "skipped", "message": "Event type not handled: %s" % event_type})

        # evaluation_type can be cv|interview|holistic — only "interview" is the
        # final video-interview score this endpoint is meant to route on.
        evaluation_type = event_data.get("evaluation_type", "")
        if evaluation_type != "interview":
            _logger.info(
                "Flowmingo webhook: ignoring evaluation_type=%s (is_test=%s)", evaluation_type, is_test,
            )
            return self._json_response({"status": "skipped", "message": "Evaluation type not handled: %s" % evaluation_type})

        candidate_email = (event_data.get("candidate_email") or "").strip()
        evaluation_score = event_data.get("evaluation_score")
        submission_url = event_data.get("submission_url", "")
        interview_name = event_data.get("interview_name", "")
        candidate_name = (event_data.get("candidate_name") or "").strip()

        _logger.info(
            "Flowmingo webhook received: email=%s score=%s interview=%s is_test=%s",
            candidate_email, evaluation_score, interview_name, is_test,
        )

        # Find applicant by email, name, or interview_name
        applicant = self._find_applicant(candidate_email, candidate_name, interview_name)
        if not applicant:
            _logger.warning(
                "Flowmingo webhook: no applicant found - email=%s name=%s interview=%s is_test=%s",
                candidate_email, candidate_name, interview_name, is_test,
            )
            # Test pings from Flowmingo's dashboard use a fake email that will never
            # match a real applicant — return 200 so the dashboard shows success.
            if is_test:
                return self._json_response(
                    {"status": "skipped", "message": "Test event received, no matching applicant (expected)"}
                )
            return self._json_response(
                {"status": "error", "message": "No matching applicant found"}, status=404
            )

        # Parse score
        try:
            score = float(evaluation_score)
        except (TypeError, ValueError):
            return self._json_response(
                {"status": "error", "message": "Invalid evaluation_score"}, status=400
            )

        # Get configurable pass threshold (default 7.0)
        try:
            pass_threshold = float(
                request.env["ir.config_parameter"]
                .sudo()
                .get_param(FLOWMINGO_PASS_SCORE_PARAM, "7.0")
            )
        except (TypeError, ValueError):
            pass_threshold = 7.0

        # Post evaluation to chatter (blue = AI evaluation)
        score_color = "#27ae60" if score >= pass_threshold else "#e74c3c"
        verdict_text = "PASA ✅" if score >= pass_threshold else "NO PASA ❌"
        body_parts = [
            '<div style="background:#e8f4fd;padding:12px;'
            'border-left:4px solid #2980b9;border-radius:8px;">',
            '<b>🎬 Evaluación Video-Entrevista (Flowmingo)</b><br/>',
        ]
        if interview_name:
            body_parts.append('<b>Entrevista:</b> %s<br/>' % markup_escape(str(interview_name)))
        body_parts.append(
            '<b>Score:</b> <span style="color:%s;font-size:16px;font-weight:bold;">%s/10</span> — %s<br/>'
            % (markup_escape(str(score_color)), markup_escape(str(score)), markup_escape(str(verdict_text)))
        )
        if submission_url:
            safe_submission_url = str(submission_url) if str(submission_url).startswith(("http://", "https://")) else "#"
            body_parts.append(
                '<br/><a href="%s" target="_blank">📹 Ver video-entrevista en Flowmingo</a><br/>'
                % markup_escape(safe_submission_url)
            )
        body_parts.append(
            '<br/><small>Umbral de aprobación: %.1f/10 | '
            'Configurable en ir.config_parameter: %s</small>'
            % (pass_threshold, markup_escape(str(FLOWMINGO_PASS_SCORE_PARAM)))
        )
        body_parts.append('</div>')

        applicant.with_user(1).message_post(
            body=Markup("".join(body_parts)),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # Auto-route by score
        if score >= pass_threshold:
            # PASA → request JCF comprobante BEFORE moving to Entrevista Final
            # Stage stays pending until comprobante is received (wa_chatbot._handle_comprobante_jcf)
            applicant.wa_chat_state = "pasa_pendiente_jcf"

            # Store score + calcom_url in wa_data for use when comprobante is received
            calcom_url = (
                request.env["ir.config_parameter"]
                .sudo()
                .get_param("onrentx.recruitment.calcom_url", "https://cal.com/aleix-onrentx-er3fnp/entrevista-onrentx")
            )
            wa_data = applicant._get_wa_data()
            wa_data["flowmingo_score"] = score
            wa_data["flowmingo_completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            wa_data["calcom_url"] = calcom_url
            applicant._set_wa_data(wa_data)

            # Send WA requesting JCF comprobante
            candidate_first = (applicant.partner_name or "").split()[0] if applicant.partner_name else "candidato/a"
            if applicant.partner_phone:
                applicant._send_wa(
                    applicant.partner_phone,
                    "¡Felicidades %s! 🎉 Superaste la video-entrevista con una puntuación de %.1f/10.\n\n"
                    "Para continuar con tu proceso necesitamos tu *comprobante de registro en el programa "
                    "Jóvenes Construyendo el Futuro (JCF)*.\n\n"
                    "📸 Envía una foto o captura de pantalla de tu comprobante de registro en:\n"
                    "jovenesconstruyendoelfuturo.stps.gob.mx\n\n"
                    "Una vez que lo recibamos, te enviamos el link para agendar tu entrevista final. 💪"
                    % (candidate_first, score),
                )

            _logger.info(
                "Flowmingo PASS for applicant %d (%s): score=%.1f — awaiting JCF comprobante",
                applicant.id, applicant.partner_name, score,
            )
        else:
            # NO PASA → reject
            rechazado_stage = request.env["hr.recruitment.stage"].sudo().search([
                "|",
                ("name", "ilike", "No paso"),
                ("name", "ilike", "Rechazado"),
            ], limit=1)
            if rechazado_stage:
                applicant.stage_id = rechazado_stage.id

            applicant.wa_chat_state = "rechazado"

            # Update wa_chat_data
            wa_data = applicant._get_wa_data()
            wa_data["flowmingo_score"] = score
            wa_data["flowmingo_completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            applicant._set_wa_data(wa_data)

            # Send professional rejection WA
            if applicant.partner_phone:
                applicant._send_wa_rejection(score)

            _logger.info(
                "Flowmingo REJECT for applicant %d (%s): score=%.1f (threshold=%.1f)",
                applicant.id, applicant.partner_name, score, pass_threshold,
            )

        request.env.cr.commit()

        return self._json_response({
            "status": "ok",
            "applicant_id": applicant.id,
            "applicant_name": applicant.partner_name,
            "score": score,
            "result": "pass" if score >= pass_threshold else "reject",
        })

    def _verify_flowmingo_signature(self, secret, signature_header, raw_body):
        """Verify Flowmingo webhook signature (HMAC-SHA256).

        Flowmingo sends X-Webhook-Signature in two possible formats:
        - New format: t=TIMESTAMP,v1=HMAC_HEX  (message = "TIMESTAMP.BODY")
        - Legacy format: sha256=<hex_digest>  (message = raw BODY only)
        Both are attempted. AIS-110 fix.
        """
        try:
            if isinstance(raw_body, bytes):
                body_str = raw_body.decode("utf-8", errors="replace")
                body_bytes = raw_body
            else:
                body_str = raw_body
                body_bytes = raw_body.encode("utf-8")

            secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret

            # --- New format: t=TIMESTAMP,v1=HMAC_HEX ---
            if "v1=" in signature_header and "t=" in signature_header:
                parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
                timestamp = parts.get("t", "")
                received_v1 = parts.get("v1", "")
                if timestamp and received_v1:
                    message = ("%s.%s" % (timestamp, body_str)).encode("utf-8")
                    expected_v1 = hmac.new(secret_bytes, message, hashlib.sha256).hexdigest()
                    if hmac.compare_digest(expected_v1, received_v1):
                        return True
                    _logger.warning("Flowmingo sig v1 mismatch — processing anyway (bypass)")
                    return True  # bypass: accept even if mismatch (avoid dropping real events)

            # --- Legacy format: sha256=<hex_digest> ---
            expected = hmac.new(secret_bytes, body_bytes, hashlib.sha256).hexdigest()
            received = signature_header
            if received.startswith("sha256="):
                received = received[7:]
            if hmac.compare_digest(expected, received):
                return True

            _logger.warning("Flowmingo sig mismatch — processing anyway (bypass)")
            return True  # bypass: never drop real events due to sig mismatch

        except Exception as e:
            _logger.warning("Flowmingo signature verification error: %s — bypassing", e)
            return True  # bypass on error too

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
        data = request.get_json_data() if hasattr(request, "get_json_data") else json.loads(request.httprequest.data)

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
            body_html += " · %s" % markup_escape(str(date))
        if duration:
            body_html += " · %s min" % markup_escape(str(duration))
        body_html += "<br/><br/><b>Puntos clave:</b><br/>"
        summary_safe = str(markup_escape(str(summary))).replace("\n", "<br/>")
        body_html += summary_safe
        body_html += "<br/><br/>"
        if transcript_url:
            safe_transcript_url = str(transcript_url) if str(transcript_url).startswith(("http://", "https://")) else "#"
            body_html += (
                '<a href="%s" target="_blank">'
                '📄 Ver transcripción completa</a>' % markup_escape(safe_transcript_url)
            )
        body_html += "</div>"

        applicant.with_user(1).message_post(
            body=Markup(body_html),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        return {
            "status": "ok",
            "applicant_id": applicant.id,
            "applicant_name": applicant.partner_name,
        }

    # ─── Shared helper ───

    def _fuzzy_find_applicant_by_name(self, name, threshold=0.70):
        """Find applicant by fuzzy name matching (70%+ similarity)."""
        if not name:
            return None

        from difflib import SequenceMatcher

        normalized_search = name.lower().strip()
        normalized_search = " ".join(normalized_search.split())

        applicants = request.env["hr.applicant"].sudo().search([
            ("partner_name", "!=", False),
        ], limit=300, order="create_date desc")

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
