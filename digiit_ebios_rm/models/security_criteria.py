from odoo import models, fields,_

class SecurityCriteria(models.Model):
    _name = 'digiit.ebios_rm.security.criteria'
    name = fields.Char(   
        string=_('Nom'),
        tracking=True,
        required=True,
        )
	