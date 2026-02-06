from odoo import models, fields,_
from odoo.tools.translate import _

class MaterialPaper(models.Model):
    _name = 'digiit.ebios_rm.material.paper'
    _inherit = 'digiit.ebios_rm.data'
    
    name=fields.Text(string=_('Material / Paper'))

        