from odoo import models, fields,_
from odoo.tools.translate import _

class ScaleImpactLevelPivot(models.Model):
    _name = 'digiit.ebios_rm.scale.impact.level.pivot'
    
    scale_impact_id = fields.Many2one(
    'digiit.ebios_rm.scale.impact',
    ondelete='cascade',    
    )
    
    scale_impact_level_id = fields.Many2one(
    'digiit.ebios_rm.scale.impact.level',
    ondelete='cascade',    
    )

	
    description = fields.Text(
        string=_('Description'),
        tracking=True,
        )
	