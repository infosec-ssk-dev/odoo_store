from odoo import models, fields,api,_

class Tag(models.Model):
    _name = 'digiit.ebios_rm.tag'
    
    name = fields.Char(
        string=_("Valeur"),
    )