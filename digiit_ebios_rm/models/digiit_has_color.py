from odoo import models, fields,_
from odoo.tools.translate import _

class DigiitHasColor(models.AbstractModel):
    _name = 'digiit.has.color'
    
    color = fields.Char(
        
        string=_('Couleur'),
        tracking=True,
        required=True,
        )
	