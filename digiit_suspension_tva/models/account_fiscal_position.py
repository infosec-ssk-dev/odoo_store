from odoo import models, fields


class AccountFiscalPosition(models.Model):
    _inherit = 'account.fiscal.position'

    # Boolean that triggers the suspension TVA logic
    digiit_suspension_tva = fields.Boolean(
        string='Suspension TVA',
        default=False,
        help='Si activé, cette position fiscale déclenche la logique de suspension TVA.',
    )
