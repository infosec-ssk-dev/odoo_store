from odoo import fields, models


class AccountAccount(models.Model):
    _inherit = 'account.account'

    digiit_is_timbre_fiscal_account = fields.Boolean(
        string='Timbre Fiscal',
        default=False,
        help='Compte timbre fiscal utilisé si aucun compte vente/achat spécifique'
    )
    digiit_is_timbre_fiscal_sale_account = fields.Boolean(
        string='Timbre Fiscal Vente',
        default=False,
        help='Compte timbre fiscal pour les factures clients'
    )
    digiit_is_timbre_fiscal_purchase_account = fields.Boolean(
        string='Timbre Fiscal Achat',
        default=False,
        help='Compte timbre fiscal pour les factures fournisseurs'
    )