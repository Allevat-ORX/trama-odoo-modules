from odoo import models, fields, api
from odoo.exceptions import UserError


class TramaTransactionClassifyWizard(models.TransientModel):
    _name = 'trama.transaction.classify.wizard'
    _description = 'Assistent Classificacio de Transaccions'

    classification = fields.Selection(
        selection=[
            ('personal', 'Personal'),
            ('onrentx', 'OnRentX'),
            ('marcatek', 'MARCATEK'),
            ('trama', 'Trama'),
            ('income', 'Ingres'),
        ],
        string='Classificacio',
        required=True,
    )
    category_id = fields.Many2one(
        'trama.expense.category',
        string='Categoria',
    )
    invoice_status = fields.Selection(
        selection=[
            ('not_needed', 'No necessaria'),
            ('pending', 'Pendent de sol\xb7licitar'),
            ('requested', 'Sol\xb7licitada'),
            ('received', 'Rebuda'),
        ],
        string='Estat factura',
    )
    transaction_count = fields.Integer(
        string='Transaccions seleccionades',
        compute='_compute_transaction_count',
    )

    @api.depends_context('active_ids')
    def _compute_transaction_count(self):
        for wiz in self:
            wiz.transaction_count = len(
                self.env.context.get('active_ids', [])
            )

    def action_classify(self):
        """Apply classification to all selected transactions."""
        self.ensure_one()
        active_ids = self.env.context.get('active_ids', [])
        if not active_ids:
            raise UserError('No hi ha transaccions seleccionades.')

        transactions = self.env['trama.personal.transaction'].browse(active_ids)

        vals = {'classification': self.classification}
        if self.category_id:
            vals['category_id'] = self.category_id.id
        if self.invoice_status:
            vals['invoice_status'] = self.invoice_status

        # Write triggers _handle_classification_change for each record
        transactions.write(vals)

        return {'type': 'ir.actions.act_window_close'}
