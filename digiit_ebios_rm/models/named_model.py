from odoo import models, fields,_
from odoo.tools.translate import _

class NamedModel(models.AbstractModel):
    _name = 'digiit.ebios_rm.named.model'
    
    name = fields.Char(

        string=_('Nom'),
        tracking=True,
        required=True,
        help=_('Pertinence')

    )
    number = fields.Integer(
        string=_('ID'),
        tracking=True,
        required=True,
        )


        
        
	