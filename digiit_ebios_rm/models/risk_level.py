from odoo import models, fields,_

class RiskLevel(models.Model):
    _name = 'digiit.ebios_rm.risk.level'
    name = fields.Integer(
        
        string=_('Niveau de risque'),
        required=True,
        )
	