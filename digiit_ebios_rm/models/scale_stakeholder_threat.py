from odoo import models, fields,_
from odoo.tools.translate import _

class ScaleStakeholderThreat(models.Model):
    _name = 'digiit.ebios_rm.scale.stakeholder.threat'
    _log_access = False
    _sql_constraints = []
    name = fields.Char(
        string=_('Nom'),
        tracking=True,
        required=True,
        )
    pivot = fields.One2many(
        'digiit.ebios_rm.stakeholder.relationtionship.threat.pivot',
        'scale_stakeholder_threat_id',
        string=_('Description'),
        tracking=True,
        required=True,
        )
	