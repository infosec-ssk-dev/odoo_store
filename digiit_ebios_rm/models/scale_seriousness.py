from odoo import models, fields,_
from odoo.tools.translate import _

class ScaleSeriousness(models.Model):
    _name = 'digiit.ebios_rm.scale.seriousness'
    _inherit = 'digiit.ebios_rm.abstract.simple.scale'
	