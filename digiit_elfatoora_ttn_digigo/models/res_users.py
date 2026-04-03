from odoo import models, fields


class ResUsers(models.Model):
    _inherit = "res.users"

    digigo_default_signer_id = fields.Many2one(
        "digiit.model.digigo.signer",
        string="Default DigiGo Signer",
        company_dependent=True,
        domain="[('company_id', '=', allowed_company_ids[0])]",
        help="The default DigiGo signer selected by this user for the current company.",
    )

