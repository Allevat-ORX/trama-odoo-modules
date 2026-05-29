from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class TramaPersonalTransaction(models.Model):
    _name = 'trama.personal.transaction'
    _description = 'Transaccion Personal'
    _order = 'date desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    CLASSIFICATION_SELECTION = [
        ('unclassified', 'Per Classificar'),
        ('personal', 'Personal'),
        ('onrentx', 'OnRentX'),
        ('marcatek', 'MARCATEK'),
        ('trama', 'Trama'),
        ('shared', 'Compartida'),
        ('income', 'Ingres'),
    ]

    STATE_SELECTION = [
        ('imported', 'Importada'),
        ('classified', 'Classificada'),
        ('processed', 'Processada'),
        ('reconciled', 'Conciliada'),
    ]

    INVOICE_STATUS_SELECTION = [
        ('not_needed', 'No necessaria'),
        ('pending', 'Pendent de sol\xb7licitar'),
        ('requested', 'Sol\xb7licitada'),
        ('received', 'Rebuda'),
    ]

    PAYMENT_METHOD_SELECTION = [
        ('credit_card', 'Targeta de credit'),
        ('debit_card', 'Targeta de debit'),
        ('transfer', 'Transferencia'),
        ('cash', 'Efectiu'),
        ('other', 'Altre'),
    ]

    SOURCE_SELECTION = [
        ('manual', 'Manual'),
        ('csv_import', 'Importacio CSV'),
        ('ocr', 'OCR'),
    ]

    # --- Core fields ---
    name = fields.Char(
        string='Descripcio',
        required=True,
        tracking=True,
    )
    date = fields.Date(
        string='Data',
        required=True,
        default=fields.Date.today,
        tracking=True,
    )
    amount = fields.Float(
        string='Import ($)',
        required=True,
        tracking=True,
        help='Positiu = despesa, negatiu = ingres',
    )

    # --- Classification ---
    classification = fields.Selection(
        CLASSIFICATION_SELECTION,
        string='Classificacio',
        default='unclassified',
        tracking=True,
        group_expand='_group_expand_classification',
    )
    society_type_id = fields.Many2one(
        'trama.society.type',
        string='Societat',
        tracking=True,
    )
    category_id = fields.Many2one(
        'trama.expense.category',
        string='Categoria',
        tracking=True,
    )

    # --- Source (for Phase 3 CSV import) ---
    source = fields.Selection(
        SOURCE_SELECTION,
        string='Origen',
        default='manual',
        readonly=True,
    )
    bank_reference = fields.Char(
        string='Referencia bancaria',
        help='ID unic de la transaccio bancaria',
        copy=False,
    )
    import_batch_id = fields.Many2one(
        'trama.bank.import.batch',
        string='Lot importacio',
        readonly=True,
        ondelete='set null',
        copy=False,
    )

    # --- Invoice tracking ---
    invoice_status = fields.Selection(
        INVOICE_STATUS_SELECTION,
        string='Estat factura',
        default='not_needed',
        tracking=True,
    )
    invoice_attachment_id = fields.Many2one(
        'ir.attachment',
        string='Factura adjunta',
    )
    receipt_attachment_id = fields.Many2one(
        'ir.attachment',
        string='Rebut adjunt',
    )

    # --- Payment ---
    payment_method = fields.Selection(
        PAYMENT_METHOD_SELECTION,
        string='Metode de pagament',
        default='credit_card',
    )

    # --- Linked records ---
    expense_ids = fields.One2many(
        'trama.society.expense',
        'transaction_id',
        string='Despeses generades',
        readonly=True,
    )
    expense_count = fields.Integer(
        string='Num. Despeses',
        compute='_compute_expense_count',
    )

    # --- Shared expense detail ---
    share_ids = fields.One2many(
        'trama.transaction.share',
        'transaction_id',
        string='Distribucio compartida',
    )

    # --- State ---
    state = fields.Selection(
        STATE_SELECTION,
        string='Estat',
        default='imported',
        tracking=True,
    )

    # --- Notes ---
    note = fields.Text(string='Notes')

    # --- Kanban color ---
    color = fields.Integer(string='Color')

    # -------------------------------------------------------
    # Computed
    # -------------------------------------------------------
    @api.depends('expense_ids')
    def _compute_expense_count(self):
        for rec in self:
            rec.expense_count = len(rec.expense_ids)

    # -------------------------------------------------------
    # group_expand for kanban -- show all columns always
    # -------------------------------------------------------
    @api.model
    def _group_expand_classification(self, classifications, domain):
        """Return all classification keys so kanban shows empty columns."""
        return [key for key, _label in self.CLASSIFICATION_SELECTION]

    # -------------------------------------------------------
    # Classification -> Society auto-mapping
    # -------------------------------------------------------
    CLASSIFICATION_TO_SOCIETY_CODE = {
        'onrentx': 'GLOBAL_RENT',
        'marcatek': 'MARCATEK',
        'trama': 'TRAMA',
    }

    @api.onchange('classification')
    def _onchange_classification(self):
        """Auto-set society_type_id when classification maps to a society."""
        code = self.CLASSIFICATION_TO_SOCIETY_CODE.get(self.classification)
        if code:
            society = self.env['trama.society.type'].search(
                [('code', '=', code)], limit=1
            )
            self.society_type_id = society.id if society else False
        elif self.classification not in ('shared',):
            # personal, income, unclassified -> clear society
            self.society_type_id = False

    # -------------------------------------------------------
    # CRUD overrides for state machine
    # -------------------------------------------------------
    def write(self, vals):
        res = super().write(vals)
        if 'classification' in vals:
            for rec in self:
                rec._handle_classification_change()
        return res

    def _handle_classification_change(self):
        """When classification changes:
        1. Delete existing linked expenses (reclassification)
        2. Auto-set society_type_id
        3. If society classification -> process immediately
        """
        # Step 1: Delete old expenses if reclassifying
        if self.expense_ids:
            self.expense_ids.unlink()

        # Step 2: Map classification to society
        code = self.CLASSIFICATION_TO_SOCIETY_CODE.get(self.classification)
        if code:
            society = self.env['trama.society.type'].search(
                [('code', '=', code)], limit=1
            )
            if society:
                self.society_type_id = society.id
                self.state = 'classified'
                self._process_single_society(society)
        elif self.classification == 'shared':
            self.state = 'classified'
            # Shared: user must fill share_ids, then call action_process_shared
        elif self.classification == 'unclassified':
            self.state = 'imported'
            self.society_type_id = False
        else:
            # personal, income
            self.state = 'classified'
            self.society_type_id = False

    # -------------------------------------------------------
    # Expense creation logic
    # -------------------------------------------------------
    def _process_single_society(self, society):
        """Create one trama.society.expense for this transaction."""
        self.ensure_one()
        if self.amount <= 0:
            # Negative or zero = income, no expense created
            self.state = 'processed'
            return
        vals = self._prepare_expense_vals(society, self.amount)
        self.env['trama.society.expense'].create(vals)
        self.state = 'processed'

    def action_process_shared(self):
        """Process a shared transaction: create N expenses from share_ids."""
        self.ensure_one()
        if self.classification != 'shared':
            raise UserError('Nomes es pot processar transaccions compartides amb aquest boto.')
        if not self.share_ids:
            raise UserError('Defineix la distribucio per societat abans de processar.')
        total_pct = sum(self.share_ids.mapped('percentage'))
        if abs(total_pct - 100.0) > 0.01:
            raise ValidationError(
                'La suma de percentatges ha de ser 100%%. Actual: %.2f%%' % total_pct
            )
        # Delete old expenses if reclassifying
        if self.expense_ids:
            self.expense_ids.unlink()
        for share in self.share_ids:
            vals = self._prepare_expense_vals(share.society_type_id, share.amount)
            self.env['trama.society.expense'].create(vals)
        self.state = 'processed'

    def _prepare_expense_vals(self, society, amount):
        """Build the vals dict for trama.society.expense.create()."""
        self.ensure_one()
        return {
            'name': self.name,
            'date': self.date,
            'society_type_id': society.id,
            'amount_total': amount,
            'category_id': self.category_id.id if self.category_id else False,
            'transaction_id': self.id,
            'state': 'draft',
            'note': self.note or '',
        }

    # -------------------------------------------------------
    # Manual state transitions
    # -------------------------------------------------------
    def action_mark_reconciled(self):
        for rec in self:
            if rec.state != 'processed':
                raise UserError('Nomes es pot conciliar transaccions processades.')
            rec.state = 'reconciled'

    def action_reset_to_imported(self):
        """Reset back to imported, delete linked expenses."""
        for rec in self:
            if rec.expense_ids:
                rec.expense_ids.unlink()
            rec.write({
                'state': 'imported',
                'classification': 'unclassified',
                'society_type_id': False,
            })
