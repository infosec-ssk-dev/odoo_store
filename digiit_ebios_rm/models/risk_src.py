from odoo import models, fields,_

class RiskSrc(models.Model):
    _name = 'digiit.ebios_rm.risk.src'
    
    
    name = fields.Char(
        string=_('Profile d\'attaquant'),
        required=True,
        )
    
    image = fields.Image("Image")
	
    description = fields.Html(
        
        string=_('Exemples et modes opératoires habituels'),
        required=True,
        )

        
    target_objective_ids = fields.Many2many(
            'digiit.ebios_rm.target.objective',
            #'risk_src_id',
            string=_('Objectif vise'),
            required=True,
            )
    