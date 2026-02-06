from odoo import models, fields,_
from odoo.tools.translate import _

class StakeholderRelationtionshipThreatPivot(models.Model):
    _name = 'digiit.ebios_rm.stakeholder.relationtionship.threat.pivot'
    
        
    description = fields.Html(
        
        string=_('Description'),
        tracking=True,
        required=True,
        )
    stakeholder_relationship_level_id = fields.Many2one(
    'digiit.ebios_rm.stakeholder.relationship.level',
    ondelete='cascade',    
    )

    scale_stakeholder_threat_id = fields.Many2one(
    'digiit.ebios_rm.scale.stakeholder.threat',
    ondelete='cascade',    
    )