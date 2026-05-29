import base64
import csv
import io
import logging
from datetime import datetime

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Date format mapping
DATE_FORMATS = {
    'dmy': ['%d/%m/%Y', '%d-%m-%Y', '%d/%m/%y', '%d-%m-%y'],
    'ymd': ['%Y-%m-%d', '%Y/%m/%d', '%y-%m-%d', '%y/%m/%d'],
    'mdy': ['%m/%d/%Y', '%m-%d-%Y', '%m/%d/%y', '%m-%d-%y'],
}

DELIMITER_MAP = {
    'comma': ',',
    'semicolon': ';',
    'tab': '\t',
    'pipe': '|',
}


class TramaBankImportWizard(models.TransientModel):
    _name = 'trama.bank.import.wizard'
    _description = 'Assistent Importacio Estat de Compte CSV'

    file_data = fields.Binary(
        string='Arxiu CSV',
        required=True,
    )
    file_name = fields.Char(
        string='Nom arxiu',
    )
    column_date = fields.Integer(
        string='Columna Data',
        default=0,
        help='Index de columna (comencant per 0)',
    )
    column_description = fields.Integer(
        string='Columna Descripcio',
        default=1,
    )
    column_amount = fields.Integer(
        string='Columna Import (unica)',
        default=2,
        help='Columna amb import unic (positiu/negatiu). Usar -1 si el CSV te columnes separades Cargo/Abono.',
    )
    column_charge = fields.Integer(
        string='Columna Cargo (despeses)',
        default=-1,
        help='Index de columna per Cargo (despeses). -1 = no usar.',
    )
    column_debit = fields.Integer(
        string='Columna Abono (ingressos)',
        default=-1,
        help='Index de columna per Abono (ingressos). -1 = no usar.',
    )
    delimiter = fields.Selection(
        [
            ('comma', 'Coma (,)'),
            ('semicolon', 'Punt i coma (;)'),
            ('tab', 'Tabulador'),
            ('pipe', 'Pipe (|)'),
        ],
        string='Delimitador',
        default='comma',
        required=True,
    )
    date_format = fields.Selection(
        [
            ('dmy', 'DD/MM/AAAA (Mexico)'),
            ('ymd', 'AAAA-MM-DD (ISO)'),
            ('mdy', 'MM/DD/AAAA (US)'),
        ],
        string='Format de data',
        default='dmy',
        required=True,
    )
    skip_header = fields.Boolean(
        string='Saltar capcalera',
        default=True,
    )
    preview_text = fields.Text(
        string='Previsualitzacio',
        compute='_compute_preview_text',
    )

    # -------------------------------------------------------
    # Preview
    # -------------------------------------------------------
    @api.depends('file_data', 'delimiter', 'skip_header')
    def _compute_preview_text(self):
        for wiz in self:
            if not wiz.file_data:
                wiz.preview_text = ''
                continue
            try:
                content = base64.b64decode(wiz.file_data).decode('utf-8-sig')
                lines = content.splitlines()[:6]  # header + 5 data lines
                delim = DELIMITER_MAP.get(wiz.delimiter, ',')
                preview_lines = []
                for i, line in enumerate(lines):
                    if i == 0 and wiz.skip_header:
                        preview_lines.append('[CAPCALERA] %s' % line)
                    else:
                        preview_lines.append('[Fila %d] %s' % (i, line))
                wiz.preview_text = '\n'.join(preview_lines)
            except Exception as e:
                wiz.preview_text = 'Error llegint arxiu: %s' % str(e)

    # -------------------------------------------------------
    # Import action
    # -------------------------------------------------------
    def action_import(self):
        """Parse CSV file and create trama.personal.transaction records."""
        self.ensure_one()
        if not self.file_data:
            raise UserError('Cal seleccionar un arxiu CSV.')

        # Decode file
        try:
            raw = base64.b64decode(self.file_data)
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                content = raw.decode('latin-1')
            except Exception:
                raise UserError('No es pot descodificar l\'arxiu. Prova amb codificacio UTF-8.')

        delim = DELIMITER_MAP.get(self.delimiter, ',')
        reader = csv.reader(io.StringIO(content), delimiter=delim)
        rows = list(reader)

        if self.skip_header and rows:
            rows = rows[1:]

        if not rows:
            raise UserError('L\'arxiu CSV esta buit o nomes te capcalera.')

        # Determine amount mode
        use_charge_debit = (self.column_charge >= 0 and self.column_debit >= 0)
        if not use_charge_debit and self.column_amount < 0:
            raise ValidationError(
                'Cal configurar la columna d\'import unic, '
                'o les columnes Cargo i Abono.'
            )

        created_ids = []
        duplicate_count = 0
        error_lines = []
        Transaction = self.env['trama.personal.transaction']

        for row_idx, row in enumerate(rows, start=2 if self.skip_header else 1):
            if not row or all(cell.strip() == '' for cell in row):
                continue  # skip empty rows

            try:
                # Parse date
                date_val = self._parse_date(row, row_idx)

                # Parse description
                description = self._parse_description(row, row_idx)

                # Parse amount
                amount = self._parse_amount(row, row_idx, use_charge_debit)

                # Check for duplicates
                if self._is_duplicate(Transaction, date_val, amount, description):
                    duplicate_count += 1
                    continue

                # Create transaction
                vals = {
                    'name': description,
                    'date': date_val,
                    'amount': amount,
                    'source': 'csv_import',
                    'state': 'imported',
                    'classification': 'unclassified',
                }
                created_ids.append(vals)

            except (UserError, ValidationError):
                raise
            except Exception as e:
                error_lines.append('Fila %d: %s' % (row_idx, str(e)))
                if len(error_lines) > 50:
                    error_lines.append('... massa errors, important parat.')
                    break

        # Create batch record first
        batch_vals = {
            'name': 'Import %s' % fields.Datetime.now().strftime('%Y-%m-%d %H:%M'),
            'file_data': self.file_data,
            'file_name': self.file_name,
            'lines_duplicate': duplicate_count,
            'state': 'done' if not error_lines else 'error',
            'error_log': '\n'.join(error_lines) if error_lines else False,
        }
        batch = self.env['trama.bank.import.batch'].create(batch_vals)

        # Create transactions linked to batch
        for vals in created_ids:
            vals['import_batch_id'] = batch.id
        if created_ids:
            Transaction.create(created_ids)

        # Return action to view the batch
        return {
            'type': 'ir.actions.act_window',
            'name': batch.name,
            'res_model': 'trama.bank.import.batch',
            'view_mode': 'form',
            'res_id': batch.id,
            'target': 'current',
        }

    # -------------------------------------------------------
    # Parsing helpers
    # -------------------------------------------------------
    def _parse_date(self, row, row_idx):
        """Extract and parse date from row."""
        if self.column_date >= len(row):
            raise UserError(
                'Fila %d: la columna de data (%d) no existeix. '
                'La fila te %d columnes.' % (row_idx, self.column_date, len(row))
            )
        raw_date = row[self.column_date].strip()
        if not raw_date:
            raise UserError('Fila %d: la data esta buida.' % row_idx)

        formats = DATE_FORMATS.get(self.date_format, DATE_FORMATS['dmy'])
        for fmt in formats:
            try:
                return datetime.strptime(raw_date, fmt).date()
            except ValueError:
                continue

        raise UserError(
            'Fila %d: no es pot interpretar la data "%s" '
            'amb el format seleccionat (%s).' % (row_idx, raw_date, self.date_format)
        )

    def _parse_description(self, row, row_idx):
        """Extract description from row."""
        if self.column_description >= len(row):
            raise UserError(
                'Fila %d: la columna de descripcio (%d) no existeix. '
                'La fila te %d columnes.' % (row_idx, self.column_description, len(row))
            )
        desc = row[self.column_description].strip()
        return desc or 'Sense descripcio (fila %d)' % row_idx

    def _parse_amount(self, row, row_idx, use_charge_debit):
        """Extract amount from row. Convention: positive = expense, negative = income."""
        if use_charge_debit:
            charge = self._safe_float(row, self.column_charge, row_idx)
            debit = self._safe_float(row, self.column_debit, row_idx)
            # Cargo (charge) = expense = positive
            # Abono (debit/credit) = income = negative
            if charge and not debit:
                return abs(charge)
            elif debit and not charge:
                return -abs(debit)
            elif charge and debit:
                # Both filled: net amount (charge positive, debit negative)
                return abs(charge) - abs(debit)
            else:
                return 0.0
        else:
            return self._safe_float(row, self.column_amount, row_idx)

    def _safe_float(self, row, col_idx, row_idx):
        """Safely extract a float value from a row column."""
        if col_idx < 0 or col_idx >= len(row):
            return 0.0
        raw = row[col_idx].strip()
        if not raw or raw == '-':
            return 0.0
        # Handle Mexican number format: $1,234.56 or 1.234,56
        raw = raw.replace('$', '').replace(' ', '')
        # If comma is decimal separator (e.g., 1.234,56)
        if ',' in raw and '.' in raw:
            if raw.rindex(',') > raw.rindex('.'):
                # Comma is decimal: 1.234,56
                raw = raw.replace('.', '').replace(',', '.')
            # else: dot is decimal: 1,234.56
            else:
                raw = raw.replace(',', '')
        elif ',' in raw and '.' not in raw:
            # Could be decimal comma: 1234,56
            # or thousands comma: 1,234 -- check digits after comma
            parts = raw.split(',')
            if len(parts) == 2 and len(parts[1]) <= 2:
                raw = raw.replace(',', '.')
            else:
                raw = raw.replace(',', '')
        try:
            return float(raw)
        except ValueError:
            _logger.warning(
                'Fila %d, columna %d: no es pot convertir "%s" a numero.',
                row_idx, col_idx, row[col_idx].strip(),
            )
            return 0.0

    # -------------------------------------------------------
    # Duplicate detection
    # -------------------------------------------------------
    def _is_duplicate(self, Transaction, date_val, amount, description):
        """Check if a transaction with same date + abs(amount) + first 20 chars exists."""
        desc_prefix = (description or '')[:20]
        domain = [
            ('date', '=', date_val),
            ('name', '=like', '%s%%' % desc_prefix.replace('%', '\\%').replace('_', '\\_')),
        ]
        candidates = Transaction.search(domain)
        for txn in candidates:
            if abs(abs(txn.amount) - abs(amount)) < 0.01:
                return True
        return False
