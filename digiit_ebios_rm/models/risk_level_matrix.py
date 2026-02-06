from odoo import models, fields,_

class RiskLevelMatrix(models.Model):
    _name = 'digiit.ebios_rm.risk.level.matrix'
    gravity = fields.Char(
        string=_('Gravité'),
        required=True,
        )
    likelihood = fields.Char(
        string=_('Vraisemblance'),
        required=True,
        )
    risk_level = fields.Selection(
        [
            ('0', _("0")),
            ('1', _("1")),
            ('2', _("2")),
            ('3', _("3")),
            ('4', _("4")),
        ],
        string=_('Niveau de risque'),
        required=True,
        )
    

	

