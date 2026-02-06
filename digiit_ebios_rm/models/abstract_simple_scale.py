from odoo import models, fields,_
from odoo.tools.translate import _

class AbstractSimpleScale(models.AbstractModel):
    _name = 'digiit.ebios_rm.abstract.simple.scale'
    
    description = fields.Text(
        
        string=_('Description'),
        tracking=True,
        required=True,
        )