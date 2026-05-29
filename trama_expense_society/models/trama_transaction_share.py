from odoo import models, fields, api


class TramaTransactionShare(models.Model):
    _name = 'trama.transaction.share'
    _description = 'Distribucio de Transaccio Compartida'
    _order = 'society_type_id'

    transaction_id = fields.Many2one(
        'trama.personal.transaction',
        string='Transaccio',
        required=True,
        ondelete='cascade',
    )
    society_type_id = fields.Many2one(
        'trama.society.type',
        string='Societat',
        required=True,
    )
    percentage = fields.Float(
        string='Percentatge (%)',
        required=True,
        default=0.0,
    )
    amount = fields.Float(
        string='Import ($)',
        compute='_compute_amount',
        store=True,
    )

    @api.depends('percentage', 'transaction_id.amount')
    def _compute_amount(self):
        for share in self:
            if share.transaction_id and share.percentage:
                share.amount = abs(share.transaction_id.amount) * (share.percentage / 100.0)
            else:
                share.amount = 0.0
