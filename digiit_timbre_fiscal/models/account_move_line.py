from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    digiit_is_timbre_fiscal = fields.Boolean(
        string='Est Timbre Fiscal',
        default=False,
        help='Identifie si cette ligne est une ligne de timbre fiscal'
    )
    digiit_exclude_from_invoice_display = fields.Boolean(
        string='Exclure de l\'affichage de la facture',
        default=False,
        help='Si coché, cette ligne n\'apparaîtra pas dans le tableau des lignes de facture'
    )