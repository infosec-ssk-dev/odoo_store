from odoo import models, fields,_
from odoo.tools.translate import _

class HasColor(models.AbstractModel):
    _name = 'digiit.ebios_rm.has.color'
    
    color = fields.Char(
        
        string=_('Couleur'),
        tracking=True,
        required=True,
        )
	