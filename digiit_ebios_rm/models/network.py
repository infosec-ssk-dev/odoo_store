from odoo import models, fields,_
from odoo.tools.translate import _

class Network(models.Model):
    _name = 'digiit.ebios_rm.network'
    
    name=fields.Char(string=_('Réseaux'))
    description = fields.Html(
        string=_('Description'),tracking=True)

        