from odoo import models, fields,_

class LikelihoodValue(models.Model):
    _name = 'digiit.ebios_rm.likelihood.value'
    name = fields.Boolean(
        
        string=_('Vraisemblance'),
        tracking=True,
        required=True,
        )
	