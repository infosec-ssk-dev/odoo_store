from odoo import models, fields,_

class TargetObjective(models.Model):
    _name = 'digiit.ebios_rm.target.objective'
    _log_access = False
    _sql_constraints = []

        
    name = fields.Char(
        string=_('Profile d\'attaquant'),
        tracking=True,
        required=True,
        )
	
    description = fields.Html(
        
        string=_('Description'),
        tracking=True,
        required=True,
        )
    
    risk_src_ids = fields.Many2many(
        'digiit.ebios_rm.risk.src',
    )	
    
    