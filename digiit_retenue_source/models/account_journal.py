from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    digiit_withholding_sales = fields.Boolean(string="Withholding Sales")
    digiit_withholding_purchases = fields.Boolean(string="Withholding Purchases")
