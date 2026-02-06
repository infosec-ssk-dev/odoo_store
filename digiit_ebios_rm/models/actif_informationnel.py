from odoo import models, fields,_
from odoo.tools.translate import _

class ActifInformationnel(models.Model):
    _name = 'digiit.ebios_rm.actif.informationnel'
    _log_access = False
    _sql_constraints = []

        
    name = fields.Char(
            string=_('Nom de l\'actif'),
            tracking=True,
            required=True,
            )
        
    classification = fields.Many2one(
            'digiit.ebios_rm.actif.informationnel.classification',
            string=_('Classification'),
            tracking=True,
            )
    business_value_id = fields.Many2one(
        'digiit.ebios_rm.business.value',
        ondelete='cascade',    
        )

    description = fields.Text(
        string=_('Description')
    )

    personne_proprietaire = fields.Text(
        string=_('Entité/ Personne Propriétaire')
    )