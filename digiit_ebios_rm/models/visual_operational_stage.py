from odoo import models, fields,_
from odoo.tools.translate import _

class VisualOperationalStage(models.Model):
    _name = 'visual.operational.stage'

    name = fields.Char(string='Nom')
    sequence = fields.Integer(default=10)    