from odoo import models, fields, _
from odoo.tools.translate import _


class BienSupport(models.Model):
    _name = 'digiit.ebios_rm.bien.support'
    _description = _('Bien support')
    _log_access = False
    _sql_constraints = []

    name = fields.Char(
        string=_('Nom du bien'),
        tracking=True,
        required=True,
    )

    classification = fields.Many2one(
        'digiit.ebios_rm.actif.informationnel.classification',  # Reuse the same classification
        string=_('Classification'),
        tracking=True,
    )

    business_value_id = fields.Many2one(
        'digiit.ebios_rm.business.value',
        string=_('Business Value'),
        ondelete='cascade',
        required=False,
    )

    description = fields.Text(
        string=_('Description')
    )

    personne_proprietaire = fields.Text(
        string=_('Entité/ Personne Propriétaire')
    )