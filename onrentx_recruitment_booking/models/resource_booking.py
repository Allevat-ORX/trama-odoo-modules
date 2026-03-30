# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from markupsafe import Markup

from odoo import api, fields, models, _

import logging

_logger = logging.getLogger(__name__)

RESCHEDULE_TEMPLATE = "onrentx_recruitment_booking.mail_template_interview_reschedule"


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    applicant_id = fields.Many2one(
        "hr.applicant",
        string="Candidato",
        compute="_compute_applicant_id",
        store=True,
    )

    @api.depends("partner_ids")
    def _compute_applicant_id(self):
        """Find linked hr.applicant for this booking's partner."""
        for booking in self:
            if booking.partner_ids:
                applicant = self.env["hr.applicant"].search([
                    ("booking_id", "=", booking.id),
                ], limit=1)
                booking.applicant_id = applicant.id if applicant else False
            else:
                booking.applicant_id = False

    def _prepare_meeting_vals(self):
        """Override to add Google Meet and clear event name."""
        vals = super()._prepare_meeting_vals()
        vals["videocall_source"] = "google_meet"
        # Set clear name: "Entrevista OnRentX: Candidato - Puesto"
        applicant = self.env["hr.applicant"].search([
            ("booking_id", "=", self.id),
        ], limit=1)
        if applicant:
            job_name = applicant.job_id.name if applicant.job_id else ""
            candidate_name = applicant.partner_name or self.partner_ids[:1].name
            if job_name:
                vals["name"] = "Entrevista OnRentX: %s - %s" % (candidate_name, job_name)
            else:
                vals["name"] = "Entrevista OnRentX: %s" % candidate_name
        return vals

    def write(self, vals):
        # Capture previous states before write
        old_states = {b.id: b.state for b in self}
        res = super().write(vals)
        if "meeting_id" in vals or "active" in vals:
            self._notify_applicant_state_change(old_states)
        return res

    def _notify_applicant_state_change(self, old_states=None):
        """Notify linked applicant when booking state changes."""
        old_states = old_states or {}
        for booking in self:
            applicant = self.env["hr.applicant"].search([
                ("booking_id", "=", booking.id),
            ], limit=1)
            if not applicant:
                continue

            old_state = old_states.get(booking.id)

            if booking.state == "scheduled":
                start_str = ""
                if booking.start:
                    start_str = fields.Datetime.context_timestamp(
                        booking, booking.start
                    ).strftime("%d/%m/%Y %H:%M")

                body_html = (
                    '<div style="background:#e3f2fd;padding:10px;'
                    'border-left:4px solid #2196F3;border-radius:6px;">'
                    '<b>📅 Entrevista agendada por el candidato</b><br/>'
                    'Fecha: <b>%s</b><br/>'
                    'Duración: %s min'
                    '</div>'
                ) % (start_str, int(booking.duration * 60))
                applicant.message_post(
                    body=Markup(body_html),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )

                # Move pipeline → Entrevista agendada (id=16)
                try:
                    applicant.stage_id = 16
                except Exception:
                    pass

                if applicant.user_id:
                    applicant.activity_schedule(
                        "mail.mail_activity_data_todo",
                        summary=_("Preparar entrevista con %s") % applicant.partner_name,
                        note=_("El candidato agendó entrevista para %s") % start_str,
                        user_id=applicant.user_id.id,
                    )

                _logger.info(
                    "Applicant %d (%s) scheduled interview for %s",
                    applicant.id, applicant.partner_name, start_str,
                )

                # Generate AI interview briefing
                try:
                    self._generate_interview_briefing(applicant, start_str)
                except Exception as e:
                    _logger.error("Briefing generation failed for %d: %s", applicant.id, e)

            elif booking.state == "confirmed":
                applicant.message_post(
                    body=Markup(
                        '<div style="background:#e8f5e9;padding:10px;'
                        'border-left:4px solid #4CAF50;border-radius:6px;">'
                        '<b>✅ Asistencia confirmada</b><br/>'
                        'El candidato confirmó su asistencia a la entrevista.'
                        '</div>'
                    ),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )

            elif booking.state == "pending" and old_state in ("scheduled", "confirmed"):
                # Event deleted from Google Calendar → reschedule
                applicant.message_post(
                    body=Markup(
                        '<div style="background:#fff3e0;padding:10px;'
                        'border-left:4px solid #FF9800;border-radius:6px;">'
                        '<b>🔄 Entrevista reagendada</b><br/>'
                        'La entrevista anterior fue cancelada. '
                        'Se envió email al candidato para que elija nuevo horario.'
                        '</div>'
                    ),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
                # Send reschedule email to candidate
                applicant._send_reschedule_email()

                _logger.info(
                    "Applicant %d (%s) interview rescheduled - email sent",
                    applicant.id, applicant.partner_name,
                )

            elif booking.state == "canceled":
                applicant.message_post(
                    body=Markup(
                        '<div style="background:#ffebee;padding:10px;'
                        'border-left:4px solid #f44336;border-radius:6px;">'
                        '<b>❌ Cita cancelada</b><br/>'
                        'La cita de entrevista fue cancelada.'
                        '</div>'
                    ),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
                # Clear booking from applicant so they can create a new one
                applicant.booking_id = False

    def _generate_interview_briefing(self, applicant, interview_date):
        """Generate AI briefing for the interviewer based on all candidate data."""
        import json
        import re
        import urllib.request

        LITELLM_URL = "http://159.54.142.132:4000/v1/chat/completions"
        LITELLM_KEY = self.env["ir.config_parameter"].sudo().get_param("onrentx.recruitment.litellm_api_key", "")

        # Gather all candidate data
        job_name = applicant.job_id.name if applicant.job_id else "No especificado"
        job_desc = ""
        if applicant.job_id and applicant.job_id.website_description:
            job_desc = re.sub(r'<[^>]+>', '', applicant.job_id.website_description)[:2000]

        # CV - extract text from PDF
        cv_text = "[Sin CV]"
        attachments = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "hr.applicant"),
            ("res_id", "=", applicant.id),
            ("mimetype", "=", "application/pdf"),
        ], limit=1)
        if attachments:
            try:
                import base64
                from io import BytesIO
                from pdfminer.high_level import extract_text
                pdf_data = base64.b64decode(attachments[0].datas)
                cv_text = extract_text(BytesIO(pdf_data))[:3000]
                _logger.info("CV text extracted for applicant %d: %d chars", applicant.id, len(cv_text))
            except Exception as e:
                cv_text = "[CV adjunto: %s — no se pudo leer: %s]" % (attachments[0].name, str(e)[:50])
                _logger.warning("CV extraction failed for applicant %d: %s", applicant.id, e)

        # Survey responses
        survey_text = ""
        try:
            responses = applicant.response_ids.filtered(lambda r: r.state == "done")
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
            survey_text = "[Sin cuestionario]"

        # WA conversation
        wa_data = {}
        try:
            wa_data = json.loads(applicant.wa_chat_data or "{}")
        except Exception:
            pass
        conversation = wa_data.get("conversation", [])
        conv_text = ""
        for m in conversation:
            role = "Candidato" if m.get("role") == "candidate" else "Bot"
            conv_text += "%s: %s\n" % (role, m.get("text", ""))
        if not conv_text:
            conv_text = "[Sin conversación WA]"

        # Previous evaluations from chatter
        eval_messages = self.env["mail.message"].sudo().search([
            ("model", "=", "hr.applicant"),
            ("res_id", "=", applicant.id),
            ("body", "ilike", "Evaluación"),
        ], limit=3)
        prev_evals = ""
        for msg in eval_messages:
            clean = re.sub(r'<[^>]+>', '', msg.body or "")[:500]
            prev_evals += clean + "\n---\n"

        # Other candidates for same position (benchmark)
        benchmark = ""
        if applicant.job_id:
            others = self.env["hr.applicant"].sudo().search([
                ("job_id", "=", applicant.job_id.id),
                ("id", "!=", applicant.id),
                ("active", "=", True),
                ("wa_chat_state", "in", ["pasa_pendiente_jcf", "listo_entrevista"]),
            ], limit=5)
            for o in others:
                o_eval = self.env["mail.message"].sudo().search([
                    ("model", "=", "hr.applicant"),
                    ("res_id", "=", o.id),
                    ("body", "ilike", "Pre-screening eval"),
                ], limit=1)
                score_info = ""
                if o_eval:
                    score_info = re.sub(r'<[^>]+>', '', o_eval.body or "")[:200]
                benchmark += "- %s: %s\n" % (o.partner_name, score_info or "sin evaluación")

        prompt = """Eres el preparador de entrevistas de OnRentX. Genera un BRIEFING DETALLADO para Aleix (CEO) que va a entrevistar a este candidato.

CANDIDATO: %s
PUESTO: %s
FECHA ENTREVISTA: %s

SOBRE ONRENTX: Plataforma tecnológica de renta de maquinaria pesada en México. Startup en crecimiento. Programa JCF ($9,582/mes).

DESCRIPCIÓN DEL PUESTO:
%s

CV DEL CANDIDATO:
%s

RESPUESTAS DEL CUESTIONARIO:
%s

CONVERSACIÓN WA PRE-SCREENING (score y lo que dijo):
%s

EVALUACIONES AI PREVIAS:
%s

OTROS CANDIDATOS PARA EL MISMO PUESTO (benchmark):
%s

GENERA EL BRIEFING EN HTML con EXACTAMENTE estas secciones:

<h4>📋 Datos cruzados (CV + Cuestionario + Bot)</h4>
Resumen de 5-6 líneas con la info clave de cada fuente. Menciona edad, estudios, experiencia real, herramientas que maneja. NO repitas lo mismo de cada fuente, CRUZA la info.

<h4>⚠️ Inconsistencias a validar</h4>
Compara lo que dijo en el cuestionario vs lo que dijo en el bot vs lo que dice el CV. Si algo no cuadra, señálalo con citas textuales entre comillas. Si todo es consistente, di "Sin inconsistencias detectadas".

<h4>🎯 Bloque 1 - Experiencia real (10 min)</h4>
3-4 preguntas MUY ESPECÍFICAS basadas en huecos o inconsistencias. Cita lo que dijo y pregunta para profundizar. Ejemplo: "En el cuestionario dijiste X pero en el bot dijiste Y. ¿Cuál es la realidad?"

<h4>🏗️ Bloque 2 - Aplicado a OnRentX (10 min)</h4>
3-4 preguntas sobre cómo aplicaría su experiencia al puesto en OnRentX. Preguntas prácticas, no teóricas. Ejemplo: "Necesitamos [tarea del puesto]. ¿Cómo lo harías concretamente?"

<h4>📊 Bloque 3 - Métricas y herramientas (5 min)</h4>
2-3 preguntas sobre herramientas y métricas relevantes al puesto.

<h4>✅ Bloque 4 - JCF y cierre (5 min)</h4>
Preguntas sobre disponibilidad, expectativa salarial vs JCF ($9,582), motivación real.

<h4>🔍 Lo que buscas validar</h4>
Lista de 3-5 puntos clave que Aleix debe confirmar en la entrevista para tomar la decisión.

<h4>📈 Benchmark</h4>
Cómo se compara este candidato vs otros del mismo puesto. Quién es mejor en qué.

<h4>🎯 Pregunta final de cierre</h4>
Al terminar la entrevista, Aleix pregunta: "Del 1 al 10, ¿qué tan interesado estás en este puesto en OnRentX?" Incluye esta pregunta y guía de interpretación: 7 o menos = interés tibio, considerar con reserva. 8-9 = interés genuino. 10 = validar si es real o complaciente, preguntar "¿qué te haría bajar ese 10?"

REGLAS ESTRICTAS — LEE ANTES DE GENERAR:
1. CRUZA TODAS LAS FUENTES: CV, cuestionario, bot WA, evaluaciones. Si algo aparece en una fuente pero no en otra, SEÑÁLALO como inconsistencia.
2. MENCIONA TODOS los empleos/experiencias del candidato, no solo el más reciente.
3. CONFRONTA al candidato con sus datos: NO "¿Cómo medías el éxito?" → SÍ "Dijiste que medías por reproducciones y compartidos. ¿Conoces CTR, CPL, ROAS?"
4. Incluye al menos UN CASO PRÁCTICO con números concretos aplicado a OnRentX.
5. El candidato NO debe poder contestar con respuestas genéricas. Si puede decir "sí tengo experiencia" sin dar detalles, la pregunta es MALA.
6. COMPARA con otros candidatos del benchmark si los hay. Quién es mejor en qué.
7. Si expectativa salarial > $9,582 JCF, PREGÚNTALO directamente: "Pediste $Xk pero JCF paga $9,582. ¿Estás de acuerdo?"
8. Cada bloque debe tener preguntas sobre DISTINTOS temas, no repetir.
9. CITA TEXTUALMENTE entre comillas lo que dijo en cada fuente.
10. El Bloque 2 DEBE incluir un caso práctico con presupuesto concreto: "Tienes $5,000 MXN mensuales para [tarea del puesto]. Dame una estrategia concreta paso a paso."
11. El Benchmark DEBE comparar fortalezas y debilidades vs cada candidato listado. No solo mencionar nombres.
12. Pon CONTEXTO de OnRentX al inicio: startup, B2B (proveedores maquinaria) + B2C (constructoras que rentan), plataforma tecnológica, equipo pequeño.
- HTML para Odoo, SIN markdown, SIN ``` """ % (
            applicant.partner_name,
            job_name,
            interview_date,
            job_desc[:1500],
            cv_text,
            survey_text[:1500],
            conv_text[:2000],
            prev_evals[:1000],
            benchmark or "Sin otros candidatos evaluados",
        )

        payload = json.dumps({
            "model": "mistral-large",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 3000,
            "temperature": 0.2,
        })

        req = urllib.request.Request(LITELLM_URL, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer %s" % LITELLM_KEY)
        req.data = payload.encode()
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read())
        briefing_html = result["choices"][0]["message"]["content"]

        # Clean markdown fences and full HTML document tags
        briefing_html = briefing_html.replace("```html", "").replace("```", "").strip()
        # Remove <html>, <head>, <style>, <body> wrappers that some LLMs add
        briefing_html = re.sub(r'<html[^>]*>|</html>', '', briefing_html)
        briefing_html = re.sub(r'<head>.*?</head>', '', briefing_html, flags=re.DOTALL)
        briefing_html = re.sub(r'<style[^>]*>.*?</style>', '', briefing_html, flags=re.DOTALL)
        briefing_html = re.sub(r'<body[^>]*>|</body>', '', briefing_html)
        briefing_html = briefing_html.strip()

        body = Markup(
            '<div style="background:#fff8e1;padding:15px;'
            'border-left:4px solid #FFC107;border-radius:8px;">'
            '<h3>📋 Briefing Pre-Entrevista — %s</h3>'
            '<p><b>Fecha:</b> %s | <b>Puesto:</b> %s</p>'
            '%s</div>'
        ) % (applicant.partner_name, interview_date, job_name, briefing_html)

        applicant.with_user(1).message_post(
            body=body,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        _logger.info(
            "Interview briefing generated for applicant %d (%s)",
            applicant.id, applicant.partner_name,
        )
