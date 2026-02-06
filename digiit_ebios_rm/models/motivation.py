from odoo import models, fields,_,api
from odoo.tools.translate import _

class Motivation(models.Model):
    _name = 'digiit.ebios_rm.motivation'
    _inherit=['digiit.ebios_rm.named.model','digiit.ebios_rm.has.color']


    matrix_id = fields.Many2one(
    'digiit.ebios_rm.pertinence.matrix',
    ondelete='cascade',    
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super(Motivation, self).create(vals_list)
        ressource_records = self.env['digiit.ebios_rm.ressource'].search([])

        matrix_vals_list = []
        for record in records:
            for resource in ressource_records:
                matrix_vals_list.append({
                    'motivation_id': record.id,
                    'ressource_id': resource.id,
                })

        if matrix_vals_list:
            self.env['digiit.ebios_rm.pertinence.matrix'].create(matrix_vals_list)

        return records
    