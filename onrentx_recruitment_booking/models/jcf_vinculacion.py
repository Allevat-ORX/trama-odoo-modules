# -*- coding: utf-8 -*-
# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
JCF Vinculación System
======================
Monitors JCF platform, sends urgent WA notifications,
tracks binding status per position.
"""

import json
import logging
import requests
import urllib.request
from datetime import datetime, timedelta

from markupsafe import Markup

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Company assignments per job category
JCF_EMPRESAS = {
    'dev': 'Jorge Martínez Hernández',
    'legal': 'Jorge Martínez Hernández',
    'proyectos': 'Comercial Ancora',
    'marketing': 'Comercial Ancora',
    'asistente': 'Comercial Ancora',
    'sac': 'Comercial Ancora',
    'ventas': 'Comercial Ancora',
    'almacenista': 'Comercial Ancora',
}

# Map job names to categories
JOB_CATEGORY_MAP = {
    'developer': 'dev',
    'full stack': 'dev',
    'legal': 'legal',
    'gestión legal': 'legal',
    'proyectos': 'proyectos',
    'presupuestos': 'proyectos',
    'marketing': 'marketing',
    'community': 'marketing',
    'asistente': 'asistente',
    'dirección': 'asistente',
    's.a.c': 'sac',
    'ventas': 'ventas',
    'almacen': 'almacenista',
}


class HrApplicantJCFVinculacion(models.Model):
    _inherit = 'hr.applicant'

    # ── JCF Vinculación fields ──
    jcf_vinculacion_nivel = fields.Selection([
        ('n1', 'Nivel 1 - Primario'),
        ('n2', 'Nivel 2 - Backup'),
    ], string="Nivel vinculación JCF", default=False)

    jcf_vinculacion_orden = fields.Integer(
        string="Orden en nivel",
        default=0,
        help="1 = primario, 2 = primer backup, etc."
    )

    jcf_vinculado = fields.Boolean(
        string="Vinculado en JCF",
        default=False,
    )

    jcf_vinculacion_aviso_enviado = fields.Datetime(
        string="Aviso WA enviado",
    )

    jcf_vinculacion_foto_recibida = fields.Boolean(
        string="Foto vinculación recibida",
        default=False,
    )

    jcf_empresa = fields.Char(
        string="Empresa JCF",
        compute="_compute_jcf_empresa",
        inverse="_inverse_jcf_empresa",
        store=True,
        readonly=False,
    )

    @api.depends('job_id')
    def _compute_jcf_empresa(self):
        for rec in self:
            if rec.job_id:
                job_lower = (rec.job_id.name or '').lower()
                for keyword, category in JOB_CATEGORY_MAP.items():
                    if keyword in job_lower:
                        rec.jcf_empresa = JCF_EMPRESAS.get(category, 'Comercial Ancora')
                        break
                else:
                    rec.jcf_empresa = 'Comercial Ancora'
            else:
                rec.jcf_empresa = ''

    def _inverse_jcf_empresa(self):
        """Allow manual override of jcf_empresa."""
        pass

    def _get_jcf_job_category(self):
        """Get the JCF category for this applicant's job."""
        if not self.job_id:
            return ''
        job_lower = (self.job_id.name or '').lower()
        for keyword, category in JOB_CATEGORY_MAP.items():
            if keyword in job_lower:
                return category
        return ''

    # ── Actions ──

    def action_send_jcf_vinculacion_wa(self):
        """Send urgent JCF binding WA to this candidate."""
        self.ensure_one()
        if not self.partner_phone:
            _logger.warning("No phone for applicant %d", self.id)
            return

        empresa = self.jcf_empresa or 'Comercial Ancora'
        phone = self.partner_phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if not phone.startswith('+'):
            if not phone.startswith('52'):
                phone = '52' + phone
            phone = '+' + phone

        msg = (
            "🚨 *URGENTE - OnRentX* 🚨\n\n"
            "¡Hola %s! La plataforma de Jóvenes Construyendo el Futuro "
            "ya está abierta para San Luis Potosí.\n\n"
            "Necesitamos que te vincules AHORA:\n\n"
            "1️⃣ Entra a jovenesconstruyendoelfuturo.stps.gob.mx\n"
            "2️⃣ Busca la empresa *\"%s\"*\n"
            "3️⃣ Solicita vinculación\n"
            "4️⃣ Cuando te acepten, envíanos captura por aquí 📸\n\n"
            "⚠️ Los cupos son limitados. Hazlo YA.\n\n"
            "Si tienes problemas, llama al 079 (atención 24h)."
        ) % (self.partner_name or '', empresa)

        self._send_wa_vinculacion(phone, msg)
        self.write({
            'jcf_vinculacion_aviso_enviado': fields.Datetime.now(),
        })

        # Log to chatter
        self.with_user(1).message_post(
            body=Markup(
                '<div style="background:#fff3cd;padding:10px;border-left:4px solid #ffc107;border-radius:6px;">'
                '🚨 <b>Aviso JCF vinculación enviado por WhatsApp</b><br/><br/>'
                '<b>Mensaje enviado:</b><br/>'
                '- Empresa a buscar: <b>%s</b><br/>'
                '- Instrucciones: Entrar a JCF, buscar empresa, solicitar vinculación, enviar captura<br/>'
                '- Teléfono: %s<br/>'
                '- Enviado: %s'
                '</div>'
            ) % (empresa, phone, fields.Datetime.now()),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

    def _send_wa_vinculacion(self, phone, message):
        """Send WA using WaSender SLP config."""
        config = self.env['onrentx.wasender.config'].sudo().search([
            ('name', 'ilike', 'San Luis'),
        ], limit=1)
        if not config:
            config = self.env['onrentx.wasender.config'].sudo().search([], limit=1)
        if not config:
            _logger.error("No WaSender config found")
            return

        api_key = config.api_key
        if not api_key:
            _logger.error("No WaSender API key")
            return

        # Clean phone
        phone_clean = phone.replace('+', '').replace(' ', '')

        try:
            resp = requests.post(
                'https://wasenderapi.com/api/send-message',
                json={'to': '+' + phone_clean, 'text': message},
                headers={
                    'Authorization': 'Bearer %s' % api_key,
                    'Content-Type': 'application/json',
                },
                timeout=30,
            )
            resp.raise_for_status()
            _logger.info("JCF WA sent to %s", phone_clean)
        except Exception as e:
            _logger.error("JCF WA send failed to %s: %s", phone_clean, e)

    # ── Mass actions ──

    def action_send_jcf_vinculacion_fase(self, fase_orden):
        """Send WA to candidates in a specific fase (by jcf_vinculacion_orden)."""
        candidates = self.search([
            ('jcf_vinculacion_nivel', '=', 'n1'),
            ('jcf_vinculacion_orden', '=', fase_orden),
            ('jcf_vinculado', '=', False),
            ('active', '=', True),
        ])
        sent_count = 0
        for candidate in candidates:
            if candidate.partner_phone and not candidate.jcf_vinculacion_aviso_enviado:
                candidate.action_send_jcf_vinculacion_wa()
                sent_count += 1
        return sent_count

    def action_send_jcf_vinculacion_masivo_n1(self):
        """Send urgent WA to all N1 candidates. Called from wizard."""
        n1_candidates = self.env['hr.applicant'].sudo().search([
            ('jcf_vinculacion_nivel', '=', 'n1'),
            ('jcf_vinculado', '=', False),
            ('active', '=', True),
        ])

        sent_count = 0
        for candidate in n1_candidates:
            if candidate.partner_phone and not candidate.jcf_vinculacion_aviso_enviado:
                candidate.action_send_jcf_vinculacion_wa()
                sent_count += 1

        # Notify Aleix
        aleix_phone = '+524424751707'
        msg = "✅ Avisos JCF enviados a %d candidatos N1.\n\nMonitoreando respuestas..." % sent_count
        self._send_wa_vinculacion_static(aleix_phone, msg)

        return sent_count

    def _send_wa_vinculacion_static(self, phone, message):
        """Static WA send (for notifications to Aleix)."""
        config = self.env['onrentx.wasender.config'].sudo().search([
            ('name', 'ilike', 'San Luis'),
        ], limit=1)
        if not config:
            config = self.env['onrentx.wasender.config'].sudo().search([], limit=1)
        if not config:
            return

        phone_clean = phone.replace('+', '').replace(' ', '')

        try:
            resp = requests.post(
                'https://wasenderapi.com/api/send-message',
                json={'to': '+' + phone_clean, 'text': message},
                headers={
                    'Authorization': 'Bearer %s' % config.api_key,
                    'Content-Type': 'application/json',
                },
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            _logger.error("Notification WA failed: %s", e)

    # ── Escalation ──

    def action_escalate_to_backup(self, job_category):
        """Escalate a position to N2 backup."""
        # Find next available N2 for this category
        all_applicants = self.env['hr.applicant'].sudo().search([
            ('jcf_vinculacion_nivel', '=', 'n2'),
            ('jcf_vinculado', '=', False),
            ('active', '=', True),
        ])

        for candidate in all_applicants:
            if candidate._get_jcf_job_category() == job_category:
                if not candidate.jcf_vinculacion_aviso_enviado:
                    candidate.action_send_jcf_vinculacion_wa()
                    return candidate

        return None

    # ── JCF Platform Monitor (cron) ──

    @api.model
    def _cron_monitor_jcf_platform(self):
        """Monitor JCF platform for ALL states - tracks open/closed/goal-reached.

        The JCF focalizacion page loads state data from dtmlp.js containing:
          var inicio = [{edo, idedo, status, value, estatus, lstatus}, ...]
          var detmun = [{mun, idmun, idedo, status, ...}, ...]

        Status mapping:
          status=1, value=100 -> ABIERTO (verde)
          status=0, value=300 -> CERRADO (rojo)
          status=0, value=200 -> META ALCANZADA (amarillo)
          Empty inicio array   -> PAGINA EN BLANCO (sin datos)

        SLP = idedo 24. Notifies Aleix only when SLP opens.
        Runs every 5 min via cron.
        """
        import re

        url = 'https://jovenesconstruyendoelfuturo.stps.gob.mx/focalizacion/dtmlp.js'
        js_content = None

        # Fetch dtmlp.js with retries
        for attempt in range(3):
            try:
                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64)')
                resp = urllib.request.urlopen(req, timeout=30)
                js_content = resp.read().decode('utf-8', errors='ignore')
                break
            except Exception as e:
                _logger.warning(
                    'JCF monitor: attempt %d failed to fetch dtmlp.js: %s',
                    attempt + 1, e,
                )
                import time
                time.sleep(5)

        if not js_content:
            _logger.error('JCF monitor: failed to fetch dtmlp.js after 3 attempts')
            return

        # Parse state entries
        try:
            inicio_pos = js_content.find('var inicio')
            if inicio_pos == -1:
                _logger.error('JCF monitor: var inicio not found in dtmlp.js')
                return

            # Check if inicio is empty
            empty_check = js_content[inicio_pos:inicio_pos + 50]
            if 'inicio = [];' in empty_check or 'inicio = []' in empty_check.replace(' ', ''):
                _logger.info('JCF monitor: PAGINA EN BLANCO - inicio vacio, sin datos')
                return

            inicio_section = js_content[inicio_pos:]
            blocks = inicio_section.split('},{')

            open_states = []      # verde (status=1, value=100)
            closed_states = []    # rojo (status=0, value=300)
            goal_states = []      # amarillo (value=200, meta alcanzada)
            slp_open = False
            slp_status = 'sin datos'

            for block in blocks:
                edo_m = re.search(r"edo:\s*'([^']+)'", block)
                status_m = re.search(r'status:\s*(\d+)', block)
                idedo_m = re.search(r'idedo:\s*(\d+)', block)
                value_m = re.search(r'value:\s*(\d+)', block)

                if not (edo_m and idedo_m):
                    continue

                edo_name = edo_m.group(1)
                status_val = int(status_m.group(1)) if status_m else 0
                idedo_val = int(idedo_m.group(1))
                value_val = int(value_m.group(1)) if value_m else 0

                # Classify by value (more reliable than status alone)
                if value_val == 100 or status_val == 1:
                    open_states.append(edo_name)
                elif value_val == 200:
                    goal_states.append(edo_name)
                else:
                    closed_states.append(edo_name)

                # Track SLP specifically
                if idedo_val == 24:
                    if value_val == 100 or status_val == 1:
                        slp_open = True
                        slp_status = 'ABIERTO'
                    elif value_val == 200:
                        slp_status = 'META ALCANZADA'
                    else:
                        slp_status = 'CERRADO'

            # Log complete status every run
            total = len(open_states) + len(closed_states) + len(goal_states)
            _logger.info(
                'JCF monitor: %d estados | '
                'ABIERTOS(%d): %s | '
                'META ALCANZADA(%d): %s | '
                'CERRADOS(%d): %s | '
                'SLP: %s',
                total,
                len(open_states),
                ', '.join(open_states) if open_states else 'ninguno',
                len(goal_states),
                ', '.join(goal_states) if goal_states else 'ninguno',
                len(closed_states),
                str(len(closed_states)) + ' estados',
                slp_status,
            )

        except Exception as e:
            _logger.error('JCF monitor: error parsing dtmlp.js: %s', e)
            return

        if slp_open:
            _logger.info('JCF PLATFORM OPEN FOR SLP!')

            # Check if we already notified to avoid spam
            param = self.env['ir.config_parameter'].sudo()
            already_triggered = param.get_param(
                'onrentx.jcf_vinculacion_triggered', 'false',
            )

            if already_triggered != 'true':
                param.set_param('onrentx.jcf_vinculacion_triggered', 'true')

                # Notify Aleix via WhatsApp (ONLY Aleix, not candidates)
                try:
                    config = self.env['onrentx.wasender.config'].sudo().browse(4)
                    api_key = config.api_key if config.exists() else None
                    if not api_key:
                        config = self.env['onrentx.wasender.config'].sudo().search(
                            [('name', 'ilike', 'San Luis')], limit=1,
                        )
                        api_key = config.api_key if config else None

                    if api_key:
                        open_list = '\n'.join('  \u2705 %s' % s for s in open_states)
                        goal_list = '\n'.join('  \U0001f7e1 %s' % s for s in goal_states)
                        msg = (
                            "\U0001f6a8 *PLATAFORMA JCF ABIERTA PARA SAN LUIS POTOSI* \U0001f6a8\n\n"
                            "La pagina de focalizacion muestra SLP como DISPONIBLE.\n\n"
                            "*Estados abiertos:*\n%s\n\n"
                        ) % open_list
                        if goal_list:
                            msg += "*Meta alcanzada:*\n%s\n\n" % goal_list
                        msg += (
                            "URL: https://jovenesconstruyendoelfuturo.stps.gob.mx/focalizacion/\n\n"
                            "Revisa y decide si lanzar avisos a candidatos."
                        )

                        resp = requests.post(
                            'https://wasenderapi.com/api/send-message',
                            json={'to': '+524424751707', 'text': msg},
                            headers={
                                'Authorization': 'Bearer %s' % api_key,
                                'Content-Type': 'application/json',
                            },
                            timeout=30,
                        )
                        resp.raise_for_status()
                        _logger.info('JCF monitor: WhatsApp notification sent to Aleix')
                    else:
                        _logger.error('JCF monitor: no WaSender API key found')
                except Exception as e:
                    _logger.error('JCF monitor: failed to send WhatsApp: %s', e)

                # Also send SMS via LabsMobile
                try:
                    import base64 as b64
                    iap = self.env['iap.account'].sudo().search([
                        ('provider', '=', 'sms_api_labsmobile'),
                    ], limit=1)
                    if iap and iap.labsmobile_username and iap.labsmobile_token:
                        sms_msg = (
                            "ALERTA JCF: SLP ABIERTO en plataforma focalizacion. "
                            "Estados abiertos: %s. "
                            "Revisar: jovenesconstruyendoelfuturo.stps.gob.mx/focalizacion/"
                        ) % ', '.join(open_states)
                        sms_auth = b64.b64encode(
                            ('%s:%s' % (iap.labsmobile_username, iap.labsmobile_token)).encode()
                        ).decode()
                        sms_payload = json.dumps({
                            'message': sms_msg,
                            'recipient': [{'msisdn': '524424751707'}],
                        })
                        sms_req = urllib.request.Request(
                            '%s/json/send' % (iap.labsmobile_base_url or 'https://api.labsmobile.com'),
                            method='POST',
                        )
                        sms_req.add_header('Content-Type', 'application/json')
                        sms_req.add_header('Authorization', 'Basic %s' % sms_auth)
                        sms_req.data = sms_payload.encode()
                        urllib.request.urlopen(sms_req, timeout=15)
                        _logger.info('JCF monitor: SMS notification sent to Aleix via LabsMobile')
                    else:
                        _logger.warning('JCF monitor: LabsMobile not configured, SMS skipped')
                except Exception as e:
                    _logger.error('JCF monitor: failed to send SMS: %s', e)
        else:
            # Reset trigger when SLP closes so next opening triggers again
            param = self.env['ir.config_parameter'].sudo()
            if param.get_param('onrentx.jcf_vinculacion_triggered', 'false') == 'true':
                param.set_param('onrentx.jcf_vinculacion_triggered', 'false')
                _logger.info('JCF monitor: SLP closed, reset trigger for next opening')

        # ── Monitor JCF Login page availability ──
        login_url = 'https://www.jovenesconstruyendoelfuturo.stps.gob.mx/login/index.php'
        login_status = 'no chequeado'
        try:
            login_req = urllib.request.Request(login_url)
            login_req.add_header('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64)')
            login_resp = urllib.request.urlopen(login_req, timeout=15)
            login_code = login_resp.getcode()
            login_body = login_resp.read().decode('utf-8', errors='ignore')

            # Check for maintenance indicators
            is_maintenance = any(w in login_body.lower() for w in [
                'mantenimiento', 'maintenance', 'fuera de servicio',
                'no disponible', 'temporalmente',
            ])

            if login_code == 200 and not is_maintenance:
                login_status = 'OK'
                _logger.info('JCF login monitor: OK (HTTP %d, login page normal)', login_code)
            elif login_code == 200 and is_maintenance:
                login_status = 'MANTENIMIENTO'
                _logger.warning('JCF login monitor: MANTENIMIENTO detectado (HTTP 200 pero pagina indica mantenimiento)')
            else:
                login_status = 'HTTP %d' % login_code
                _logger.warning('JCF login monitor: HTTP %d', login_code)
        except Exception as e:
            login_status = 'CAIDO'
            _logger.error('JCF login monitor: CAIDA - %s', e)

            # Notify Aleix if login page is down
            param_login = self.env['ir.config_parameter'].sudo()
            already_notified_down = param_login.get_param('onrentx.jcf_login_down_notified', 'false')
            if already_notified_down != 'true':
                param_login.set_param('onrentx.jcf_login_down_notified', 'true')
                try:
                    config = self.env['onrentx.wasender.config'].sudo().browse(4)
                    if config.exists() and config.api_key:
                        down_msg = (
                            "\u26a0\ufe0f *JCF Login CAIDO* \u26a0\ufe0f\n\n"
                            "La pagina de login/vinculacion no responde:\n"
                            "%s\n\n"
                            "Error: %s"
                        ) % (login_url, str(e))
                        resp = requests.post(
                            'https://wasenderapi.com/api/send-message',
                            json={'to': '+524424751707', 'text': down_msg},
                            headers={
                                'Authorization': 'Bearer %s' % config.api_key,
                                'Content-Type': 'application/json',
                            },
                            timeout=15,
                        )
                        resp.raise_for_status()
                        _logger.info('JCF login monitor: WhatsApp alert sent - page down')
                except Exception as wa_e:
                    _logger.error('JCF login monitor: failed to send alert: %s', wa_e)
            return

        # Reset down notification when page comes back
        param_login = self.env['ir.config_parameter'].sudo()
        if param_login.get_param('onrentx.jcf_login_down_notified', 'false') == 'true':
            param_login.set_param('onrentx.jcf_login_down_notified', 'false')
            _logger.info('JCF login monitor: page recovered, reset down alert')

        # ── 30-min summary DISABLED (2026-04-03) - too noisy, only reopen alerts needed ──
        # To re-enable, uncomment the block below
        return
        # ── 30-min summary WhatsApp to Aleix ──
        param_summary = self.env['ir.config_parameter'].sudo()
        run_count = int(param_summary.get_param('onrentx.jcf_monitor_run_count', '0'))
        run_count += 1
        param_summary.set_param('onrentx.jcf_monitor_run_count', str(run_count))

        if run_count >= 6:  # 6 runs * 5 min = 30 min
            param_summary.set_param('onrentx.jcf_monitor_run_count', '0')
            try:
                config = self.env['onrentx.wasender.config'].sudo().browse(4)
                if config.exists() and config.api_key:
                    # Build status parts
                    if open_states:
                        open_txt = ', '.join(open_states)
                    else:
                        open_txt = 'ninguno'
                    if goal_states:
                        goal_txt = ', '.join(goal_states)
                    else:
                        goal_txt = 'ninguno'

                    summary = (
                        "*Monitor JCF - Reporte 30min*\n\n"
                        "ABIERTOS (%d): %s\n"
                        "META ALCANZADA (%d): %s\n"
                        "CERRADOS: %d estados\n\n"
                        "*SLP: %s*\n"
                        "*Login page: %s*\n\n"
                        "Cron activo cada 5 min | Siguiente reporte en 30 min"
                    ) % (
                        len(open_states), open_txt,
                        len(goal_states), goal_txt,
                        len(closed_states),
                        slp_status,
                        login_status,
                    )

                    import requests as req_lib
                    resp = req_lib.post(
                        'https://wasenderapi.com/api/send-message',
                        json={'to': '+524424751707', 'text': summary},
                        headers={
                            'Authorization': 'Bearer %s' % config.api_key,
                            'Content-Type': 'application/json',
                        },
                        timeout=15,
                    )
                    resp.raise_for_status()
                    _logger.info('JCF monitor: 30-min summary sent to Aleix')
            except Exception as e:
                _logger.error('JCF monitor: failed to send 30-min summary: %s', e)


    # ── Escalation check (cron) ──

    @api.model
    def _cron_check_jcf_escalation(self):
        """Check N1 candidates without response after 24 hours. Runs every 15 min. DEDUP: only 1 alert per candidate."""
        twenty_four_hours_ago = fields.Datetime.now() - timedelta(hours=24)

        # Find N1 candidates who were notified 2+ hours ago but haven't responded
        stale_n1 = self.env['hr.applicant'].sudo().search([
            ('jcf_vinculacion_nivel', '=', 'n1'),
            ('jcf_vinculado', '=', False),
            ('jcf_vinculacion_aviso_enviado', '!=', False),
            ('jcf_vinculacion_aviso_enviado', '<', twenty_four_hours_ago),
            ('jcf_vinculacion_foto_recibida', '=', False),
            ('active', '=', True),
        ])

        for candidate in stale_n1:
            # DEDUP: skip if already escalated
            data = candidate._get_wa_data() if hasattr(candidate, '_get_wa_data') else {}
            if data.get('escalation_sent'):
                continue
            category = candidate._get_jcf_job_category()
            if not category:
                continue

            # Check if position already filled
            filled = self.env['hr.applicant'].sudo().search([
                ('jcf_vinculado', '=', True),
                ('active', '=', True),
            ])
            filled_categories = set()
            for f in filled:
                filled_categories.add(f._get_jcf_job_category())

            if category in filled_categories:
                continue  # Position already filled

            # Notify Aleix
            job_name = candidate.job_id.name if candidate.job_id else category
            backup = self.env['hr.applicant'].sudo().search([
                ('jcf_vinculacion_nivel', '=', 'n2'),
                ('jcf_vinculado', '=', False),
                ('jcf_vinculacion_aviso_enviado', '=', False),
                ('active', '=', True),
            ])

            backup_for_category = None
            for b in backup:
                if b._get_jcf_job_category() == category:
                    backup_for_category = b
                    break

            if backup_for_category:
                msg = (
                    "⚠️ *%s* no respondió en 24 horas para *%s*.\n\n"
                    "Backup disponible: *%s* (%s)\n\n"
                    "¿Activo al backup? Responde SÍ o ve a Odoo."
                ) % (
                    candidate.partner_name,
                    job_name,
                    backup_for_category.partner_name,
                    backup_for_category.partner_phone or 'sin tel',
                )
            else:
                msg = (
                    "⚠️ *%s* no respondió en 24 horas para *%s*.\n\n"
                    "No hay backup disponible para esta posición."
                ) % (candidate.partner_name, job_name)

            aleix_phone = '+524424751707'
            self._send_wa_vinculacion_static(aleix_phone, msg)

    # ── Status dashboard ──

    @api.model
    def get_jcf_vinculacion_status(self):
        """Get status of all JCF binding positions."""
        result = {}

        # Get all candidates with vinculacion nivel
        candidates = self.env['hr.applicant'].sudo().search([
            ('jcf_vinculacion_nivel', 'in', ['n1', 'n2']),
            ('active', '=', True),
        ])

        for c in candidates:
            category = c._get_jcf_job_category()
            if category not in result:
                result[category] = {
                    'job': c.job_id.name if c.job_id else category,
                    'empresa': c.jcf_empresa,
                    'n1': [],
                    'n2': [],
                    'vinculado': False,
                }

            entry = {
                'id': c.id,
                'name': c.partner_name,
                'phone': c.partner_phone,
                'aviso_enviado': bool(c.jcf_vinculacion_aviso_enviado),
                'foto_recibida': c.jcf_vinculacion_foto_recibida,
                'vinculado': c.jcf_vinculado,
                'orden': c.jcf_vinculacion_orden,
            }

            if c.jcf_vinculacion_nivel == 'n1':
                result[category]['n1'].append(entry)
            else:
                result[category]['n2'].append(entry)

            if c.jcf_vinculado:
                result[category]['vinculado'] = True

        return result

    # ── Closure ──

    @api.model
    def _check_all_positions_filled(self):
        """Check if all positions have at least 1 vinculado."""
        status = self.get_jcf_vinculacion_status()
        all_filled = all(v['vinculado'] for v in status.values())

        if all_filled and status:
            # Notify Aleix
            msg = "✅ *TODOS LOS PUESTOS CUBIERTOS* ✅\n\n"
            for cat, data in status.items():
                vinculado_name = ''
                for n in data['n1'] + data['n2']:
                    if n['vinculado']:
                        vinculado_name = n['name']
                        break
                msg += "• %s: %s ✅\n" % (data['job'], vinculado_name)

            msg += "\n¡Proceso de vinculación completado!"

            aleix_phone = '+524424751707'
            self._send_wa_vinculacion_static(aleix_phone, msg)

            # Send thanks to unused backups
            unused = self.env['hr.applicant'].sudo().search([
                ('jcf_vinculacion_nivel', '=', 'n2'),
                ('jcf_vinculado', '=', False),
                ('jcf_vinculacion_aviso_enviado', '!=', False),
                ('active', '=', True),
            ])
            for u in unused:
                if u.partner_phone:
                    phone = u.partner_phone.replace(' ', '').replace('-', '')
                    if not phone.startswith('+'):
                        if not phone.startswith('52'):
                            phone = '52' + phone
                        phone = '+' + phone
                    thanks_msg = (
                        "Hola %s, gracias por tu interés en OnRentX y por tu disposición "
                        "en el proceso de vinculación JCF. En esta ocasión los cupos ya "
                        "fueron cubiertos, pero te tendremos en cuenta para futuras "
                        "vacantes. ¡Mucho éxito! 🙌"
                    ) % (u.partner_name or '')
                    self._send_wa_vinculacion_static(phone, thanks_msg)

        return all_filled

    # ── Handle vinculacion photo in webhook ──

    def handle_jcf_vinculacion_photo(self):
        """Called when a candidate in N1/N2 sends a photo after the vinculacion WA."""
        self.ensure_one()
        if not self.jcf_vinculacion_nivel:
            return False

        if self.jcf_vinculado:
            return False  # Already linked

        self.write({
            'jcf_vinculacion_foto_recibida': True,
        })

        # Send confirmation
        phone = self.partner_phone or ''
        phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if not phone.startswith('+'):
            if not phone.startswith('52'):
                phone = '52' + phone
            phone = '+' + phone

        msg = (
            "✅ Perfecto %s, tu captura de vinculación está registrada.\n\n"
            "Te contactaremos para tu fecha de inicio y onboarding. "
            "¡Bienvenido/a a OnRentX! 🎉"
        ) % (self.partner_name or '')
        self._send_wa_vinculacion(phone, msg)

        # Notify Aleix
        category = self._get_jcf_job_category()
        job_name = self.job_id.name if self.job_id else category
        aleix_msg = (
            "📸 *%s* envió captura de vinculación para *%s*.\n\n"
            "Revísala en Odoo y confirma con el botón 'Confirmar Vinculación'."
        ) % (self.partner_name, job_name)
        self._send_wa_vinculacion_static('+524424751707', aleix_msg)

        # Log to chatter
        self.with_user(1).message_post(
            body=Markup(
                '<div style="background:#d4edda;padding:10px;border-left:4px solid #28a745;border-radius:6px;">'
                '📸 <b>Captura de vinculación JCF recibida</b><br/>'
                'Revisar adjunto y confirmar vinculación.'
                '</div>'
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        return True

    def action_confirm_jcf_vinculacion(self):
        """Manually confirm JCF binding after reviewing photo."""
        self.ensure_one()
        self.write({'jcf_vinculado': True})

        category = self._get_jcf_job_category()
        job_name = self.job_id.name if self.job_id else category

        # Log
        self.with_user(1).message_post(
            body=Markup(
                '<div style="background:#d4edda;padding:10px;border-left:4px solid #28a745;border-radius:6px;">'
                '✅ <b>Vinculación JCF CONFIRMADA</b><br/>'
                'Puesto: %s | Empresa: %s'
                '</div>'
            ) % (job_name, self.jcf_empresa or ''),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        # Notify Aleix
        aleix_msg = "✅ *%s* vinculado para *%s* ✅" % (self.partner_name, job_name)
        self._send_wa_vinculacion_static('+524424751707', aleix_msg)

        # Check if all positions filled
        self._check_all_positions_filled()

    # ── JCF Vinculación FAQ Bot ──

    def handle_jcf_vinculacion_incoming(self, text):
        """Handle incoming WA messages from candidates in vinculación phase."""
        self.ensure_one()
        if not self.jcf_vinculacion_nivel:
            return False

        # Check for image (vinculación proof)
        if text.startswith('[Imagen:') or text.startswith('[imagen:'):
            return self.handle_jcf_vinculacion_photo()

        phone = self.partner_phone or ''
        phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if not phone.startswith('+'):
            if not phone.startswith('52'):
                phone = '52' + phone
            phone = '+' + phone

        empresa = self.jcf_empresa or 'Comercial Ancora'
        job_name = self.job_id.name if self.job_id else 'tu puesto'

        # Log incoming to chatter
        from markupsafe import Markup as M
        self.with_user(1).message_post(
            body=M(
                '<div style="background:#fff9c4;padding:8px;border-left:3px solid #f9a825;border-radius:4px;">'
                '📱 WhatsApp recibido de %s:<br/>%s</div>'
            ) % (self.partner_name or '', text),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        # Call LLM for intelligent response
        import json, urllib.request
        litellm_url = "http://159.54.142.132:4000/v1/chat/completions"
        litellm_key = self.env['ir.config_parameter'].sudo().get_param(
            'onrentx.recruitment.litellm_api_key', '')

        prompt = """Eres el asistente de reclutamiento de OnRentX. El candidato %s está en proceso de vinculación JCF.

CONTEXTO:
- Puesto: %s
- Empresa JCF donde debe vincularse: %s
- El candidato debe entrar a jovenesconstruyendoelfuturo.stps.gob.mx y buscar la empresa "%s"
- Cuando se vincule, debe enviarnos una captura de pantalla por WhatsApp
- Los cupos son limitados
- JCF paga $9,582.47/mes + IMSS + capacitación 12 meses
- Requisitos: 18-29 años, no estudiar ni trabajar formalmente
- Si tiene problemas: llamar al 079 (atención 24h)
- Las entrevistas son digitales por Google Meet
- OnRentX es una plataforma de renta de maquinaria pesada en México, startup en SLP
- Ubicación trabajo: San Luis Potosí, zona Garita de Jalisco
- Horario trabajo: se define en la entrevista, somos flexibles

REGLAS:
- Responde CORTO (2-3 líneas máximo)
- Si pregunta algo que no sabes: "Eso lo platicamos cuando inicies"
- Si pide hablar con humano: "Le paso tu mensaje al equipo, te contactarán pronto"
- NUNCA inventes fechas ni horarios específicos
- NUNCA digas que la plataforma está abierta si no lo sabes
- Sé amable pero directo

MENSAJE DEL CANDIDATO: %s

Responde solo el mensaje, sin formato, sin prefijo.""" % (
            self.partner_name or '', job_name, empresa, empresa, text
        )

        try:
            payload = json.dumps({
                "model": "groq-llama",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.3,
            })

            req = urllib.request.Request(litellm_url, method='POST')
            req.add_header('Content-Type', 'application/json')
            req.add_header('Authorization', 'Bearer %s' % litellm_key)
            req.data = payload.encode()
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
            response = result['choices'][0]['message']['content'].strip()
        except Exception as e:
            _logger.error("JCF FAQ LLM error: %s", e)
            response = (
                "Gracias por tu mensaje. Si necesitas ayuda con la vinculación JCF, "
                "entra a jovenesconstruyendoelfuturo.stps.gob.mx y busca \"%s\". "
                "Si tienes problemas, llama al 079." % empresa
            )

        # Send response
        self._send_wa_vinculacion(phone, response)

        # Log bot response to chatter
        self.with_user(1).message_post(
            body=M(
                '<div style="background:#e8f5e9;padding:8px;border-left:3px solid #4caf50;border-radius:4px;">'
                '🤖 Bot JCF enviado → %s<br/>%s</div>'
            ) % (phone, response),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        # Detect human handoff request
        text_lower = text.lower()
        human_keywords = ['hablar con alguien', 'hablar con una persona', 'comunicarme con',
                          'humano', 'persona real', 'alguien de la empresa']
        if any(kw in text_lower for kw in human_keywords):
            self._send_wa_vinculacion_static('+524424751707',
                "💬 *%s* (%s) quiere hablar con humano.\nMensaje: %s" % (
                    self.partner_name, job_name, text[:200]))

        return True


    # ── Unified JCF Monitor Cron (Phase 10) ──

    @api.model
    def _cron_unified_jcf_monitor(self):
        """
        Unified JCF monitoring cron.
        Replaces: cron_jcf_platform_monitor + system crontab jcf_municipios_monitor.py

        Monitors:
          1. JCF platform state (SLP state open/closed)
          2. Municipality status (SLP + Soledad de Graciano Sanchez)
          3. JCF login page availability

        Only alerts on SIGNIFICANT CHANGES (not every run).
        Stores last known status in ir.config_parameter.
        Sends consolidated alert to Aleix via WA only on change.
        """
        import urllib.request
        import re
        from datetime import datetime

        ICP = self.env['ir.config_parameter'].sudo()
        _logger.info("Unified JCF monitor: starting run at %s", datetime.utcnow().isoformat())

        # ── 1. Check login page availability ──
        login_available = False
        try:
            req = urllib.request.Request(
                'https://jovenesconstruyendoelfuturo.stps.gob.mx/',
                headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64)'},
            )
            resp = urllib.request.urlopen(req, timeout=15)
            login_available = resp.status in (200, 301, 302)
        except Exception as e:
            _logger.warning("JCF unified: login page check failed: %s", e)
            login_available = False

        prev_login = ICP.get_param('onrentx.jcf_monitor.login_available', '')
        login_changed = prev_login and (str(login_available) != prev_login)
        ICP.set_param('onrentx.jcf_monitor.login_available', str(login_available))

        # ── 2. Fetch dtmlp.js for state + municipality data ──
        js_content = None
        try:
            url = 'https://jovenesconstruyendoelfuturo.stps.gob.mx/focalizacion/dtmlp.js'
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64)'})
            resp = urllib.request.urlopen(req, timeout=30)
            js_content = resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            _logger.warning("JCF unified: failed to fetch dtmlp.js: %s", e)

        slp_status = 'sin_datos'
        soledad_status = 'sin_datos'
        alerts = []

        if js_content:
            # Parse SLP state status (idedo=24)
            try:
                inicio_pos = js_content.find('var inicio')
                if inicio_pos != -1:
                    empty_check = js_content[inicio_pos:inicio_pos + 60]
                    if 'inicio = [];' not in empty_check.replace(' ', ''):
                        inicio_section = js_content[inicio_pos:]
                        blocks = inicio_section.split('},{')
                        for block in blocks:
                            idedo_m = re.search(r'idedo:\s*(\d+)', block)
                            status_m = re.search(r'status:\s*(\d+)', block)
                            value_m = re.search(r'value:\s*(\d+)', block)
                            if not idedo_m:
                                continue
                            if int(idedo_m.group(1)) == 24:  # SLP
                                val = int(value_m.group(1)) if value_m else 0
                                sts = int(status_m.group(1)) if status_m else 0
                                if val == 100 or sts == 1:
                                    slp_status = 'abierto'
                                elif val == 200:
                                    slp_status = 'meta_alcanzada'
                                else:
                                    slp_status = 'cerrado'
            except Exception as e:
                _logger.warning("JCF unified: error parsing estado data: %s", e)

            # Parse municipality status for SLP (id=25) and Soledad (id=37)
            # idedo=24 is SLP state. Municipality IDs within SLP:
            # San Luis Potosí city (capital) and Soledad de Graciano Sánchez
            try:
                detmun_pos = js_content.find('var detmun')
                if detmun_pos != -1:
                    detmun_section = js_content[detmun_pos:]
                    mun_blocks = detmun_section.split('},{')
                    for block in mun_blocks:
                        idedo_m = re.search(r'idedo:\s*(\d+)', block)
                        if not idedo_m or int(idedo_m.group(1)) != 24:
                            continue
                        mun_m = re.search(r"mun:\s*'([^']+)'", block)
                        status_m = re.search(r'status:\s*(\d+)', block)
                        value_m = re.search(r'value:\s*(\d+)', block)
                        if not mun_m:
                            continue
                        mun_name = mun_m.group(1).lower()
                        val = int(value_m.group(1)) if value_m else 0
                        sts = int(status_m.group(1)) if status_m else 0
                        mun_open = (val == 100 or sts == 1)
                        if 'soledad' in mun_name or 'graciano' in mun_name:
                            soledad_status = 'abierto' if mun_open else ('meta_alcanzada' if val == 200 else 'cerrado')
            except Exception as e:
                _logger.warning("JCF unified: error parsing municipio data: %s", e)

        # ── 3. Detect significant changes ──
        prev_slp = ICP.get_param('onrentx.jcf_monitor.slp_status', 'unknown')
        prev_soledad = ICP.get_param('onrentx.jcf_monitor.soledad_status', 'unknown')

        slp_changed = prev_slp not in ('unknown', 'sin_datos') and slp_status != prev_slp
        soledad_changed = prev_soledad not in ('unknown', 'sin_datos') and soledad_status != prev_soledad

        # Save current status
        ICP.set_param('onrentx.jcf_monitor.slp_status', slp_status)
        ICP.set_param('onrentx.jcf_monitor.soledad_status', soledad_status)

        _logger.info(
            "JCF unified: SLP=%s (prev=%s, changed=%s) | Soledad=%s (prev=%s, changed=%s) | Login=%s",
            slp_status, prev_slp, slp_changed,
            soledad_status, prev_soledad, soledad_changed,
            login_available,
        )

        # ── 4. Alert only on significant events ──
        # SLP reopening is the critical event (was closed/unknown, now open)
        if slp_changed and slp_status == 'abierto':
            alerts.append("🚨 *SAN LUIS POTOSÍ ABIERTO* en JCF! Antes: %s" % prev_slp)

        if soledad_changed and soledad_status == 'abierto':
            alerts.append("🟢 *Soledad de Graciano Sánchez ABIERTA* en JCF!")

        if login_changed and not login_available:
            alerts.append("⚠️ Página JCF no disponible (antes sí lo estaba)")

        if login_changed and login_available and prev_login == 'False':
            alerts.append("✅ Página JCF disponible de nuevo")

        # SLP trigger (legacy compatibility: mark as triggered when SLP opens)
        if slp_status == 'abierto':
            prev_triggered = ICP.get_param('onrentx.jcf_vinculacion_triggered', 'false')
            if prev_triggered != 'true':
                ICP.set_param('onrentx.jcf_vinculacion_triggered', 'true')
                alerts.append("📋 Primer detección: SLP abierto. Candidatos N1 listos para vincular.")

        if not alerts:
            _logger.info("JCF unified: no significant changes detected, skipping alert")
            return

        # ── 5. Send consolidated alert to Aleix ──
        alert_text = "\n".join(alerts)
        msg = (
            "📡 *Monitor JCF - Cambio detectado*\n\n"
            "%s\n\n"
            "Estado actual:\n"
            "- SLP: %s\n"
            "- Soledad G.S.: %s\n"
            "- Login JCF: %s\n\n"
            "Hora: %s UTC"
        ) % (
            alert_text,
            slp_status.upper(),
            soledad_status.upper(),
            "OK" if login_available else "NO DISPONIBLE",
            datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        )

        try:
            config = self.env['onrentx.wasender.config'].sudo().browse(4)
            if not config.exists() or not config.api_key:
                config = self.env['onrentx.wasender.config'].sudo().search(
                    [('api_key', '!=', False)], limit=1
                )
            if config and config.api_key:
                import requests as req_lib
                req_lib.post(
                    'https://wasenderapi.com/api/send-message',
                    json={'to': '+524424751707', 'text': msg},
                    headers={
                        'Authorization': 'Bearer %s' % config.api_key,
                        'Content-Type': 'application/json',
                    },
                    timeout=15,
                )
                _logger.info("JCF unified: alert sent to Aleix: %s", alert_text[:100])
        except Exception as e:
            _logger.error("JCF unified: failed to send alert to Aleix: %s", e)
