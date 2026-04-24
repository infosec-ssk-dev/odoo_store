from odoo import models, fields


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # FODEC boolean under Position Fiscal section (Vente et Achat tab)
    digiit_fodec = fields.Boolean(
        string='FODEC',
        default=False,
    )

    # Autorisations suspension TVA linked to this partner
    digiit_autorisation_ids = fields.One2many(
        'digiit.autorisation', 'partner_id', string='Autorisations Suspension TVA'
    )
