from odoo import models, fields,_
from odoo.tools.translate import _

class Application(models.Model):
    _name = 'digiit.ebios_rm.application'
    
    name=fields.Char(string=_('Application / Software'),
        tracking=True)
    description = fields.Html(
        string=_('Description'),tracking=True)

        