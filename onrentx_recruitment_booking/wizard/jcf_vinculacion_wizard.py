# -*- coding: utf-8 -*-
import logging
from markupsafe import Markup

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class JCFVinculacionWizard(models.TransientModel):
    _name = 'jcf.vinculacion.wizard'
    _description = 'Panel de Vinculación JCF'

    status_html = fields.Html(
        string="Estado",
        compute="_compute_status_html",
    )

    @api.depends_context('lang')
    def _compute_status_html(self):
        for rec in self:
            status = self.env['hr.applicant'].get_jcf_vinculacion_status()

            html = '<table style="width:100%;border-collapse:collapse;">'
            html += (
                '<tr style="background:#f0f0f0;">'
                '<th style="padding:8px;border:1px solid #ddd;">Puesto</th>'
                '<th style="padding:8px;border:1px solid #ddd;">Empresa JCF</th>'
                '<th style="padding:8px;border:1px solid #ddd;">N1 Primario</th>'
                '<th style="padding:8px;border:1px solid #ddd;">Estado</th>'
                '<th style="padding:8px;border:1px solid #ddd;">N2 Backups</th>'
                '</tr>'
            )

            for category, data in sorted(status.items()):
                # N1 primary
                n1_names = []
                for n in sorted(data['n1'], key=lambda x: x.get('orden', 99)):
                    icon = '✅' if n['vinculado'] else ('📸' if n['foto_recibida'] else ('📨' if n['aviso_enviado'] else '⏳'))
                    n1_names.append('%s %s' % (icon, n['name']))

                # N2 backups
                n2_names = []
                for n in sorted(data['n2'], key=lambda x: x.get('orden', 99)):
                    icon = '✅' if n['vinculado'] else ('📸' if n['foto_recibida'] else ('📨' if n['aviso_enviado'] else '⏳'))
                    n2_names.append('%s %s' % (icon, n['name']))

                # Position status
                if data['vinculado']:
                    pos_status = '<span style="color:green;font-weight:bold;">✅ CUBIERTO</span>'
                elif any(n['foto_recibida'] for n in data['n1']):
                    pos_status = '<span style="color:orange;font-weight:bold;">📸 FOTO RECIBIDA</span>'
                elif any(n['aviso_enviado'] for n in data['n1']):
                    pos_status = '<span style="color:blue;">📨 AVISO ENVIADO</span>'
                else:
                    pos_status = '<span style="color:gray;">⏳ PENDIENTE</span>'

                html += (
                    '<tr>'
                    '<td style="padding:8px;border:1px solid #ddd;font-weight:bold;">%s</td>'
                    '<td style="padding:8px;border:1px solid #ddd;">%s</td>'
                    '<td style="padding:8px;border:1px solid #ddd;">%s</td>'
                    '<td style="padding:8px;border:1px solid #ddd;">%s</td>'
                    '<td style="padding:8px;border:1px solid #ddd;">%s</td>'
                    '</tr>'
                ) % (
                    data['job'],
                    data['empresa'],
                    '<br/>'.join(n1_names) if n1_names else '-',
                    pos_status,
                    '<br/>'.join(n2_names) if n2_names else '-',
                )

            html += '</table>'

            # Monitor status
            cron = self.env.ref('onrentx_recruitment_booking.cron_jcf_platform_monitor', raise_if_not_found=False)
            monitor_active = cron.active if cron else False
            monitor_status = '🟢 ACTIVO' if monitor_active else '🔴 APAGADO'

            html = (
                '<div style="margin-bottom:15px;">'
                '<b>Monitoreo plataforma JCF:</b> %s'
                '</div>'
            ) % monitor_status + html

            # Legend
            html += (
                '<div style="margin-top:15px;font-size:12px;color:#666;">'
                '⏳ Pendiente | 📨 Aviso enviado | 📸 Foto recibida | ✅ Vinculado'
                '</div>'
            )

            rec.status_html = Markup(html)

    def action_send_n1(self):
        """Send WA to all N1 primaries."""
        count = self.env['hr.applicant'].action_send_jcf_vinculacion_masivo_n1()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Avisos JCF enviados',
                'message': '%d avisos enviados a candidatos N1' % count,
                'type': 'success',
            },
        }

    def action_activate_monitor(self):
        """Activate the JCF platform monitoring cron."""
        cron = self.env.ref('onrentx_recruitment_booking.cron_jcf_platform_monitor', raise_if_not_found=False)
        if cron:
            cron.write({'active': True})
        cron2 = self.env.ref('onrentx_recruitment_booking.cron_jcf_escalation_check', raise_if_not_found=False)
        if cron2:
            cron2.write({'active': True})

        # Reset trigger flag
        self.env['ir.config_parameter'].sudo().set_param('onrentx.jcf_vinculacion_triggered', 'false')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Monitoreo activado',
                'message': 'Cron JCF cada 5 min + escalamiento cada 15 min',
                'type': 'warning',
            },
        }

    def action_deactivate_monitor(self):
        """Deactivate the JCF platform monitoring cron."""
        cron = self.env.ref('onrentx_recruitment_booking.cron_jcf_platform_monitor', raise_if_not_found=False)
        if cron:
            cron.write({'active': False})
        cron2 = self.env.ref('onrentx_recruitment_booking.cron_jcf_escalation_check', raise_if_not_found=False)
        if cron2:
            cron2.write({'active': False})

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Monitoreo pausado',
                'message': 'Crons JCF desactivados',
                'type': 'info',
            },
        }
