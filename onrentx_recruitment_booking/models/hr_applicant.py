# -*- coding: utf-8 -*-
"""
HR Applicant Extensions for Recruitment Pipeline
Adds computed fields for time tracking and scoring
"""
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime
import logging

_logger = logging.getLogger(__name__)


class HrApplicant(models.Model):
    _inherit = 'hr.applicant'

    # Time tracking computed fields
    days_to_screen = fields.Float(
        string='Days to Pre-screening',
        compute='_compute_stage_duration',
        store=True,
        help='Days from application to pre-screening evaluation'
    )

    days_to_interview = fields.Float(
        string='Days to Interview',
        compute='_compute_stage_duration',
        store=True,
        help='Days from application to scheduled interview'
    )

    days_to_hire = fields.Float(
        string='Days to Hire',
        compute='_compute_stage_duration',
        store=True,
        help='Days from application to hire/closure'
    )

    # Additional tracking fields
    prescreening_eval = fields.Char(
        string='Pre-screening Evaluation Date',
        help='Date when WA chatbot evaluation was completed'
    )

    jcf_attachment_id = fields.Many2one(
        'ir.attachment',
        string='JCF Comprobante'
    )

    x_comprobante_filename = fields.Char(
        string='Comprobante Filename'
    )

    contract_sent = fields.Boolean(
        string='Contract Sent',
        default=False,
        help='Indicates if contract has been sent to candidate'
    )

    survey_score = fields.Float(
        string='Survey Score',
        help='Score from completed survey'
    )

    wa_chat_score = fields.Float(
        string='WA Chat Score',
        help='Score from WhatsApp chatbot evaluation'
    )

    booking_start = fields.Datetime(
        string='Interview Start',
        help='Scheduled interview start time'
    )

    booking_id = fields.Many2one(
        'resource.booking',
        string='Interview Booking',
        help='Linked resource booking for interview'
    )

    booking_state = fields.Selection([
        ('pending', 'Pending'),
        ('scheduled', 'Scheduled'),
        ('confirmed', 'Confirmed'),
        ('canceled', 'Canceled'),
    ], string='Booking State', compute='_compute_booking_state', store=True)

    booking_portal_url = fields.Char(
        string='Booking Portal URL',
        help='URL for candidate to schedule interview'
    )

    wa_chat_data = fields.Text(
        string='WA Chat Data',
        help='JSON data from WhatsApp chatbot conversation'
    )

    is_jcf_candidate = fields.Boolean(
        string='Candidato JCF Junio 2026',
        default=False,
        help='Indica si el candidato es parte de la tanda JCF Junio 2026'
    )

    @api.depends('booking_id', 'booking_id.state')
    def _compute_booking_state(self):
        """Compute booking state from related resource.booking"""
        for applicant in self:
            if applicant.booking_id:
                applicant.booking_state = applicant.booking_id.state
            else:
                applicant.booking_state = 'pending'

    def action_view_booking(self):
        """Open the linked interview booking."""
        self.ensure_one()
        if not self.booking_id:
            return
        return {
            "type": "ir.actions.act_window",
            "name": "Entrevista",
            "res_model": "resource.booking",
            "res_id": self.booking_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_confirm_jcf(self):
        """Confirm JCF and send interview appointment. Delegates to vinculacion method."""
        self.ensure_one()
        return self.action_confirm_jcf_vinculacion()

    def action_create_interview_booking(self):
        """Create interview booking and send email to candidate."""
        self.ensure_one()
        if self.booking_id:
            raise UserError(_("Ya existe una cita de entrevista para este candidato."))

        BookingType = self.env['resource.booking.type']
        booking_type = BookingType.search([], limit=1)
        if not booking_type:
            raise UserError(_("No hay tipos de cita configurados. Configure uno en Citas > Tipos."))

        Booking = self.env['resource.booking']
        booking = Booking.create({
            'type_id': booking_type.id,
            'partner_id': self.partner_id.id if self.partner_id else False,
            'name': _("Entrevista: %s") % (self.partner_name or self.display_name),
        })
        self.booking_id = booking.id
        if hasattr(booking, 'portal_url') and booking.portal_url:
            self.booking_portal_url = booking.portal_url

        self.message_post(
            body=_("Cita de entrevista creada. URL: %s") % (self.booking_portal_url or 'pendiente'),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return True

    def action_copy_booking_url(self):
        """Return booking URL for clipboard copy via client action."""
        self.ensure_one()
        if not self.booking_portal_url:
            raise UserError(_("No hay URL de cita disponible."))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("URL copiada"),
                'message': self.booking_portal_url,
                'type': 'info',
                'sticky': False,
            },
        }

    def action_cancel_interview_booking(self):
        """Cancel the interview booking."""
        self.ensure_one()
        if not self.booking_id:
            raise UserError(_("No hay cita de entrevista para cancelar."))
        if hasattr(self.booking_id, 'action_cancel'):
            self.booking_id.action_cancel()
        else:
            self.booking_id.write({'state': 'canceled'})
        self.message_post(
            body=_("Cita de entrevista cancelada."),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return True

    @api.depends('create_date', 'prescreening_eval', 'booking_start', 'date_closed')
    def _compute_stage_duration(self):
        """Compute time spent in each stage"""
        for applicant in self:
            create_date = applicant.create_date

            if not create_date:
                applicant.days_to_screen = 0
                applicant.days_to_interview = 0
                applicant.days_to_hire = 0
                continue

            # Days to pre-screening
            if applicant.prescreening_eval:
                try:
                    eval_date = datetime.strptime(applicant.prescreening_eval, '%Y-%m-%d')
                    applicant.days_to_screen = (eval_date - create_date.replace(tzinfo=None)).days
                except:
                    applicant.days_to_screen = 0
            else:
                applicant.days_to_screen = 0

            # Days to interview
            if applicant.booking_start:
                interview_date = applicant.booking_start.replace(tzinfo=None) if applicant.booking_start.tzinfo else applicant.booking_start
                applicant.days_to_interview = (interview_date - create_date.replace(tzinfo=None)).days
            else:
                applicant.days_to_interview = 0

            # Days to hire
            if applicant.date_closed:
                closed_date = applicant.date_closed.replace(tzinfo=None) if applicant.date_closed.tzinfo else applicant.date_closed
                applicant.days_to_hire = (closed_date - create_date.replace(tzinfo=None)).days
            else:
                applicant.days_to_hire = 0

    def get_ranking_score(self):
        """Calculate overall ranking score for candidate"""
        self.ensure_one()
        score = 0.0
        weights = {
            'survey': 0.3,
            'chat': 0.3,
            'speed': 0.4
        }

        # Survey score component
        if self.survey_score:
            score += self.survey_score * weights['survey']

        # Chat score component
        if self.wa_chat_score:
            score += self.wa_chat_score * weights['chat']

        # Speed component (faster is better, max 10 points)
        if self.days_to_interview and self.days_to_interview > 0:
            speed_score = max(0, 10 - (self.days_to_interview / 3))
            score += speed_score * weights['speed']

        return round(score, 2)
