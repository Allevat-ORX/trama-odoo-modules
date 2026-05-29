from odoo import models, fields, api


class TramaBankImportBatch(models.Model):
    _name = 'trama.bank.import.batch'
    _description = 'Lot Importacio Bancaria'
    _order = 'date_import desc, id desc'

    STATE_SELECTION = [
        ('draft', 'Esborrany'),
        ('done', 'Completat'),
        ('error', 'Error'),
    ]

    name = fields.Char(
        string='Nom',
        default=lambda self: 'Import %s' % fields.Datetime.now().strftime('%Y-%m-%d %H:%M'),
        required=True,
    )
    date_import = fields.Datetime(
        string='Data importacio',
        default=fields.Datetime.now,
        readonly=True,
    )
    file_data = fields.Binary(
        string='Arxiu CSV',
        attachment=True,
    )
    file_name = fields.Char(
        string='Nom arxiu',
    )
    transaction_ids = fields.One2many(
        'trama.personal.transaction',
        'import_batch_id',
        string='Transaccions importades',
    )
    lines_total = fields.Integer(
        string='Linies totals',
        compute='_compute_import_stats',
        store=True,
    )
    lines_imported = fields.Integer(
        string='Linies importades',
        compute='_compute_import_stats',
        store=True,
    )
    lines_duplicate = fields.Integer(
        string='Linies duplicades',
    )
    date_from = fields.Date(
        string='Data inici',
        compute='_compute_date_range',
        store=True,
    )
    date_to = fields.Date(
        string='Data fi',
        compute='_compute_date_range',
        store=True,
    )
    state = fields.Selection(
        STATE_SELECTION,
        string='Estat',
        default='draft',
    )
    error_log = fields.Text(
        string='Log errors',
    )
    note = fields.Text(
        string='Notes',
    )

    # -------------------------------------------------------
    # Computed
    # -------------------------------------------------------
    @api.depends('transaction_ids')
    def _compute_import_stats(self):
        for rec in self:
            imported_count = len(rec.transaction_ids)
            rec.lines_imported = imported_count
            rec.lines_total = imported_count + rec.lines_duplicate

    @api.depends('transaction_ids.date')
    def _compute_date_range(self):
        for rec in self:
            dates = rec.transaction_ids.mapped('date')
            dates = [d for d in dates if d]
            rec.date_from = min(dates) if dates else False
            rec.date_to = max(dates) if dates else False
