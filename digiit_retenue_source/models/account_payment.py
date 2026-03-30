from odoo import fields, models


class AccountPayment(models.Model):
    _inherit = "account.payment"

    digiit_numero_document = fields.Char(string="Numero de document")
    digiit_clearing_date = fields.Date(string="Date d'echeance")
    digiit_source_bank_id = fields.Many2one(
        "res.bank",
        string="Banque source",
    )
    digiit_journal_type = fields.Selection(related="journal_id.type", string="Journal Type")
