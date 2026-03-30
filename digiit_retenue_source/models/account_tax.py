from odoo import fields, models


class AccountTax(models.Model):
    _inherit = "account.tax"

    digiit_retenu_tax = fields.Boolean(string="Retenu Tax")
