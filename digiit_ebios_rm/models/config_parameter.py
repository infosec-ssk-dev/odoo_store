from odoo import models, fields,_

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'
    

    accepted_risk_level = fields.Float(string=_('Niveau de risque accepté'), config_parameter='digiit_ebios_rm.accepted_risk_level')
    accepted_risk_level_sc_strategic = fields.Float(string=_('Niveau de gravité accepté ( scenarios strategiques )'), config_parameter='digiit_ebios_rm.accepted_risk_level_sc_strategic')
	