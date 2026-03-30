from odoo import fields, models


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

    digiit_numero_document = fields.Char(string="Numero de document")
    digiit_clearing_date = fields.Date(string="Date d'echeance")
    digiit_source_bank_id = fields.Many2one(
        "res.bank",
        string="Banque source",
    )
    digiit_journal_type = fields.Selection(related="journal_id.type", string="Journal Type")

    def _create_payment_vals_from_wizard(self, batch_result):
        vals = super()._create_payment_vals_from_wizard(batch_result)
        vals.update(
            {
                "digiit_numero_document": self.digiit_numero_document,
                "digiit_clearing_date": self.digiit_clearing_date,
                "digiit_source_bank_id": self.digiit_source_bank_id.id,
            }
        )
        return vals