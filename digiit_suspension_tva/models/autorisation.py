from odoo import models, fields, _


class DigiitAutorisation(models.Model):
    _name = 'digiit.autorisation'
    _description = 'Autorisation Suspension TVA'
    _order = 'date_debut desc'
    _rec_name = 'num_autorisation'

    partner_id = fields.Many2one(
        'res.partner', string='Client', required=True, ondelete='cascade'
    )
    num_autorisation = fields.Char(string='Num Autorisation', required=True)
    type_autorisation = fields.Selection(
        selection=[
            ('suspension_tva', 'Suspension TVA'),
            ('suspension_tva_fodec', 'Suspension TVA et FODEC'),
        ],
        string='Type',
        required=True,
    )
    date_debut = fields.Date(string='Date début', required=True)
    date_effet = fields.Date(string='Date effet')
    date_fin = fields.Date(string='Date fin')
    attachment = fields.Binary(string='Pièce jointe')
    attachment_filename = fields.Char(string='Nom du fichier')

    _sql_constraints = [
        ('num_autorisation_uniq', 'unique(num_autorisation)', _('Le numéro d\'autorisation doit être unique.')),
    ]
