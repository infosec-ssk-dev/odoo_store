from odoo import models, fields


class ResUsers(models.Model):
    _inherit = "res.users"

    last_signing_method = fields.Selection(
        [
            ("digigo", "DigiGo"),
            ("enterprise", "Cachet électronique Enterprise-ID"),
            ("token", "Certificat ID-Trust (USB)"),
        ],
        string="Last Used Signing Method",
        company_dependent=True,
        help="The last signing method used by this user for the current company. "
        "Pre-selected the next time the submission wizard is opened.",
    )

