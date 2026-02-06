from odoo import models, fields,_
from odoo.tools.translate import _

class ScaleThreatLevel(models.Model):
    _name = 'digiit.ebios_rm.scale.threat.level'
    _inherit = 'digiit.ebios_rm.abstract.simple.scale'


    color = fields.Integer(
        string=_('Couleur'),
        tracking=True,
        required=True,
        )

    threat_zone = fields.Char( 
        string=_('Zone de risque'),
        tracking=True,
        required=True,
        )
	

    
        