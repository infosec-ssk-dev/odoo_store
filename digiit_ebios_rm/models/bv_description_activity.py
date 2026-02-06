from odoo import models, fields,_
from odoo.tools.translate import _

class BvDescriptionActivity(models.Model):
    _name = 'digiit.ebios_rm.bv.description.activity'
    _inherit = 'digiit.ebios_rm.data'
    
    name=fields.Text(string=_('Description / Activité'))


        