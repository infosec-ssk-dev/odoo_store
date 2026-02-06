from odoo import models, fields,_
from odoo.tools.translate import _


class StudyObject(models.Model):
    _name = 'digiit.ebios_rm.study.object'
    name = fields.Text(string=_('Study object'), required=True);
    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_('Etude'),
        ondelete='cascade'
    )





        