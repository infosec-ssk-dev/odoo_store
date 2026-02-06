from odoo import models, fields, api, _


class Activity(models.Model):
    _name = 'digiit.ebios_rm.activity'
    _inherit = ['digiit.ebios_rm.named.model', 'digiit.ebios_rm.has.color']

    matrix_id = fields.Many2one(
        'digiit.ebios_rm.pertinence.matrix',
        ondelete='cascade',
    )
