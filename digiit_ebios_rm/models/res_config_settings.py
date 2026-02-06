from odoo import models, fields,_
from odoo.tools.translate import _

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    motivation_ids = fields.One2many(
        'motivation',
        'id',
        string=_('Motivations'),
        tracking=True,
        )
    ressource_ids = fields.One2many(
        'ressource',
        'id',
        string=_('Ressources'),
        tracking=True,
        required=True,
        )
    
    pertinence_ids = fields.One2many(
        'pertinence',
        'id',
        string=_('Pertinence'),
        tracking=True,
        )
    
	
    pertinence_matrix = fields.One2many(
        'pertinence.matrix',
        'id',
        string=_('Matrice des pertinences'),
        tracking=True,
        )




        