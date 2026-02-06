from odoo import models, fields,_
from odoo.tools.translate import _

class ScaleRiskDetails(models.Model):
    _name = 'digiit.ebios_rm.scale.risk.details'
    
    num = fields.Integer(
        string=_('Nombre'),
        tracking=True,
        required=True,
        )
	