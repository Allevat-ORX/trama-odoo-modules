import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TramaExpenseInvoice(models.Model):
    _name = 'trama.expense.invoice'
    _description = 'Factura de Proveïdor per Expense'
    _order = 'date desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    STATE_SELECTION = [
        ('draft', 'Borrador'),
        ('pending', 'Pendent de Rebre'),
        ('received', 'Rebuda'),
        ('cancelled', 'Cancel·lada'),
    ]

    expense_id = fields.Many2one(
        'trama.society.expense',
        string='Expense',
        required=True,
        ondelete='cascade',
        tracking=True,
    )
    vendor_id = fields.Many2one(
        'res.partner',
        string='Proveïdor',
        required=True,
        tracking=True,
    )
    invoice_number = fields.Char(
        string='Número Factura',
        required=True,
        tracking=True,
    )
    amount = fields.Float(
        string='Import ($)',
        required=True,
        tracking=True,
    )
    date = fields.Date(
        string='Data Factura',
        required=True,
        default=fields.Date.today,
        tracking=True,
    )
    state = fields.Selection(
        STATE_SELECTION,
        string='Estat',
        default='draft',
        required=True,
        tracking=True,
    )
    attachment_ids = fields.Many2many(
        'ir.attachment',
        string='Adjunts (PDF/XML)',
    )
    note = fields.Text(string='Notes')

    # Computed: expense reference
    society_type_id = fields.Many2one(
        'trama.society.type',
        string='Societat',
        related='expense_id.society_type_id',
        store=True,
    )
    category_id = fields.Many2one(
        'trama.expense.category',
        string='Categoria',
        related='expense_id.category_id',
        store=True,
    )

    # -------------------------------------------------------
    # Constraints
    # -------------------------------------------------------
    _sql_constraints = [
        (
            'invoice_number_vendor_uniq',
            'unique(invoice_number, vendor_id)',
            'El número de factura + proveïdor ha de ser únic.',
        ),
    ]

    # -------------------------------------------------------
    # Actions
    # -------------------------------------------------------
    def action_mark_pending(self):
        """Mark invoice as pending (sent to vendor, waiting for response)."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError('Només es pot marcar com pendent des de Borrador.')
            rec.state = 'pending'
        return True

    def action_mark_received(self):
        """Mark invoice as received (vendor sent the invoice)."""
        for rec in self:
            if rec.state not in ['draft', 'pending']:
                raise UserError('Només es pot marcar com rebuda des de Borrador o Pendent.')
            rec.state = 'received'
        return True

    def action_cancel(self):
        """Cancel invoice."""
        for rec in self:
            if rec.state == 'received':
                raise UserError('No es pot cancel·lar una factura rebuda. Contacta amb l\'administrador.')
            rec.state = 'cancelled'
        return True

    def action_reset_to_draft(self):
        """Reset invoice to draft state."""
        for rec in self:
            rec.state = 'draft'
        return True
