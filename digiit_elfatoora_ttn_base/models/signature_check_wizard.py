from odoo import models, fields


class ElfatooraSignatureCheckWizard(models.TransientModel):
    _name = "digiit_elfatoora.signature_check_wizard"
    _description = "Elfatoora Signature Check"

    invoice_id = fields.Many2one("account.move", string="Invoice", readonly=True)
    source = fields.Selection(
        [
            ("signed", "Signed XML"),
            ("ttn", "TTN XML"),
        ],
        string="Source",
        readonly=True,
    )

    result_text = fields.Html(string="Verification Result", readonly=True, sanitize=False)

