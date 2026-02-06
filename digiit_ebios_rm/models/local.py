from odoo import models, fields,_
from odoo.tools.translate import _

class Local(models.Model):
    _name = 'digiit.ebios_rm.local'
    
    name=fields.Char(string=_('Emplacement'))
    description = fields.Html(
        string=_('Description'),tracking=True)


        