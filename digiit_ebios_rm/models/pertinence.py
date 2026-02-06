from odoo import models, fields,_
from odoo.tools.translate import _

class Pertinence(models.Model):
    _name = 'digiit.ebios_rm.pertinence'
    _inherit=['digiit.ebios_rm.named.model','digiit.ebios_rm.has.color']

        
    retenu = fields.Boolean(
        string=_('Retenu?'),
        tracking=True,
        required=True,
        )
    matrix_id = fields.Many2one(
    'digiit.ebios_rm.pertinence.matrix',
    ondelete='cascade',    
    )
    risk_source_id = fields.Many2one(
    'digiit.ebios_rm.risk.source',
    ondelete='cascade',    
    )