from odoo import models, fields,_
from odoo.tools.translate import _

class ActifInformationnelClassification(models.Model):
    _name = 'digiit.ebios_rm.actif.informationnel.classification'
    _log_access = False
    _sql_constraints = []

        
    name = fields.Char(
        
        string=_('Nom'),
        tracking=True,
        required=True,
        )
	