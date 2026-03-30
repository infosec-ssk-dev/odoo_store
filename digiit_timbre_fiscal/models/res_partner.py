from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    digiit_exemption_timbre_fiscal = fields.Boolean(
        string='Exception du timbre fiscal',
        default=False,
        help='Si coché, le timbre fiscal ne sera pas ajouté aux factures de ce partenaire'
    )
