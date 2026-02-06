from odoo import models, fields,_
from odoo.tools.translate import _

class UnderStudy(models.AbstractModel):
    _name = 'digiit.under.study'
    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_("Etude"),
        ondelete='cascade'
    )

        