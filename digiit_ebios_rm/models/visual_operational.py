from odoo import models, fields,_
from odoo.tools.translate import _

class VisualOperational(models.Model):
    _name = 'visual.operational'
    
    
    name = fields.Char(string='Titre')
    value = fields.Integer(string='Valeur')
    stage_id = fields.Many2one('visual.operational.stage', string='Stage') 
    
    image = fields.Binary(string='Image', attachment=True, store=True)	
    study_id = fields.Many2one(
        'study',
        ondelete='cascade',    
    )
    dependency_ids = fields.Many2many(
        'visual.operational',
        string=_('Dépendences'),
        tracking=True,
    )
	