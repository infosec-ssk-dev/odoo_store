from odoo import models, fields, api


class DigiitBonCommandeVise(models.Model):
    _name = 'digiit.bon.commande.vise'
    _description = 'Bon de Commande Visé Vente'
    _order = 'date_facture desc'

    # --- Fields from the invoice (read-only) ---
    move_id = fields.Many2one(
        'account.move', string='Facture', required=True,
        domain=[('move_type', '=', 'out_invoice'),
                ('fiscal_position_id.digiit_suspension_tva', '=', True)],
        ondelete='cascade',
    )
    date_facture = fields.Date(
        string='Date de Facturation',
        related='move_id.invoice_date',
        store=True,
        readonly=True,
    )
    numero_facture = fields.Char(
        string='Numéro Facture',
        related='move_id.name',
        store=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Client',
        related='move_id.partner_id',
        store=True,
        readonly=True,
    )
    montant_ht = fields.Monetary(
        string='Montant HT',
        related='move_id.amount_untaxed',
        store=True,
        readonly=True,
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='move_id.currency_id',
        store=True,
        readonly=True,
    )

    # Position fiscale label (read-only display)
    digiit_fiscal_position_name = fields.Char(
        related='move_id.fiscal_position_id.name',
        string='Type (Position Fiscale)',
        readonly=True,
        store=True,
    )

    # --- Fields editable by user ---
    digiit_autorisation_id = fields.Many2one(
        'digiit.autorisation',
        string='Numéro Autorisation',
        domain="[('partner_id', '=', partner_id)]",
    )
    digiit_num_bc = fields.Char(string='Num BC')
    digiit_date_bc = fields.Date(string='Date BC')
    digiit_attachment = fields.Binary(string='Pièce jointe BC')
    digiit_attachment_filename = fields.Char(string='Nom du fichier BC')

    # --- Status ---
    digiit_statut = fields.Selection(
        selection=[
            ('en_cours', 'En cours'),
            ('cloturee', 'Clôturée'),
        ],
        string='Statut',
        compute='_compute_digiit_statut',
        store=True,
        readonly=False,  # allow manual override to re-edit
    )

    @api.depends('digiit_num_bc', 'digiit_date_bc', 'digiit_attachment')
    def _compute_digiit_statut(self):
        for rec in self:
            if rec.digiit_num_bc and rec.digiit_date_bc and rec.digiit_attachment:
                rec.digiit_statut = 'cloturee'
            else:
                rec.digiit_statut = 'en_cours'
