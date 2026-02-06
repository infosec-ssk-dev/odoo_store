from odoo import models, fields,_
from odoo.tools.translate import _

class Matrix(models.TransientModel):
    _name = 'digiit.ebios_rm.matrix'
    _log_access = True
    
    motivation_ids = fields.Many2many(
        'digiit.ebios_rm.motivation',
        string=_('Motivations'),
        tracking=True,
        required=True,
        )
    ressource_ids = fields.Many2many(
        'digiit.ebios_rm.ressource',
        string=_('Ressources'),
        tracking=True,
        required=True,
        )
    
    pertinence_ids = fields.Many2many(
        'digiit.ebios_rm.pertinence',
        string=_('Pertinence'),
        tracking=True,
        required=True,
        )
    
	
    pertinence_matrix = fields.Many2many(
        'digiit.ebios_rm.pertinence.matrix',
        string=_('Matrices des pertinences'),
        tracking=True,
        required=True,
        )
	