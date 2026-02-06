from odoo import models, fields,_
from odoo.tools.translate import _

class AbstractBVData(models.AbstractModel):
    _name = 'digiit.ebios_rm.data'
    _inherit = ['mail.thread', 'mail.activity.mixin']


    name = fields.Char(string=_('Nom'),Tracking=True)
    business_value_id = fields.Many2one(
        'digiit.ebios_rm.business.value',        
        string=_('Valeur métier'),   
        ondelete='cascade',
        tracking=True
    )
    