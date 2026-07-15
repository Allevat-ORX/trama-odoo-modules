# -*- coding: utf-8 -*-
"""
HR Job Extensions for Recruitment Pipeline
Adds Flowmingo interview URL per job position
"""
from odoo import models, fields

class HrJob(models.Model):
    _inherit = "hr.job"

    flowmingo_interview_url = fields.Char(
        string="Flowmingo Interview URL",
        help="URL de la video-entrevista Flowmingo para este puesto. "
             "Si está vacío, se usa el parámetro global onrentx.flowmingo_interview_url",
    )
