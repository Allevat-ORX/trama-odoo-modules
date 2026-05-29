from odoo import models, api


class TramaSocietyExpenseReport(models.AbstractModel):
    """Reporte de Gastos (para usar con report_action)"""
    _name = 'report.trama_expense_society.report_expense_list'
    _description = 'Reporte de Gastos'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['trama.society.expense'].browse(docids)
        return {
            'doc_ids': docids,
            'doc_model': 'trama.society.expense',
            'docs': docs,
        }