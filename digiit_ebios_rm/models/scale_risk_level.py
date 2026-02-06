from odoo import models, fields,_, api
from odoo.tools.translate import _

class ScaleRiskLevel(models.Model):
    _name = 'digiit.ebios_rm.scale.risk.level'
    _inherit = 'digiit.ebios_rm.abstract.simple.scale'

    
    name = fields.Text(
        
        string=_('Nom'),
        tracking=True,
        required=True, 
        readonly=True
    )
    
    description = fields.Html(
        
        string=_('Description'),
        tracking=True,
        required=True,
        )
    
    @api.model
    def create(self, vals):
        next_number = self.env['digiit.ebios_rm.scale.risk.level'].search_count([]) + 1
        vals['name'] = f"{next_number}"
        return super(ScaleRiskLevel, self).create(vals)
	