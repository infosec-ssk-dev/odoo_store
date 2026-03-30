# In models/account_tax.py
from odoo import fields, models


class AccountTax(models.Model):
    _inherit = 'account.tax'

    digiit_is_timbre_fiscal_tax = fields.Boolean(
        string='Est Taxe Timbre Fiscal',
        default=False,
        help='Marquer cette taxe comme taxe de timbre fiscal'
    )