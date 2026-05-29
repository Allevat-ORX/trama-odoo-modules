from . import models
from . import reports
from . import wizard


def _create_default_society_types(env):
    """Create default society types after module installation"""
    env['trama.society.type'].create_default_societies()


def _create_default_expense_categories(env):
    """Create the 7 default expense categories from Plan Financiero"""
    env['trama.expense.category'].create_default_categories()


def post_init_hook(env):
    """Post initialization hook (Odoo 18 signature)"""
    _create_default_society_types(env)
    _create_default_expense_categories(env)
