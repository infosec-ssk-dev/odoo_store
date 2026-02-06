from odoo import models, fields,_

class GravityValue(models.Model):
    _name = 'digiit.ebios_rm.gravity.value'
    

    name = fields.Integer(
        
        string=_('Gravité'),
        tracking=True,
        required=True,
        )
	