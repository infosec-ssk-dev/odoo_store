from odoo import models, fields,_
from odoo.tools.translate import _

class StakeholderRelationshipLevel(models.Model):
    _name = 'digiit.ebios_rm.stakeholder.relationship.level'
    
    
    name = fields.Char(
        string=_('Nombre'),
        tracking=True,
        required=True,
        )
    
    pivot = fields.One2many(
        'digiit.ebios_rm.stakeholder.relationtionship.threat.pivot',
        'stakeholder_relationship_level_id',
        string=_('Risque'),
        tracking=True,
        required=True,
        )
	