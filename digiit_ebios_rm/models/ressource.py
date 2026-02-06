from odoo import models, fields,_,api
from odoo.tools.translate import _

class Ressource(models.Model):
    _name = 'digiit.ebios_rm.ressource'
    _inherit=['digiit.ebios_rm.named.model','digiit.ebios_rm.has.color']

    matrix_id = fields.Many2one(
    'digiit.ebios_rm.pertinence.matrix',
    ondelete='cascade',    
    )

    risk_source_id = fields.Many2one(
            'digiit.ebios_rm.risk.source',
            string=_('Ressources'),
            tracking=True,
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super(Ressource, self).create(vals_list)
        motivation_records = self.env['digiit.ebios_rm.motivation'].search([])

        matrix_vals_list = []
        for record in records:
            for motivation in motivation_records:
                matrix_vals_list.append({
                    'motivation_id': motivation.id,
                    'ressource_id': record.id,
                })

        if matrix_vals_list:
            self.env['digiit.ebios_rm.pertinence.matrix'].create(matrix_vals_list)

        return records