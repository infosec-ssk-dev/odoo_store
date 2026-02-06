from odoo import models, fields, api, _
from odoo.tools.translate import _


class PertinenceMatrix(models.Model):
    _name = 'digiit.ebios_rm.pertinence.matrix'
    _log_access = False
    _sql_constraints = [('unique_motivation_ressource', 'UNIQUE(motivation_id, ressource_id)', '')]

    ressource_id = fields.Many2one(
        'digiit.ebios_rm.ressource',
        string=_('Ressource'),
        tracking=True,
        required=True,
    )

    pertinence_id = fields.Many2one(
        'digiit.ebios_rm.pertinence',
        string=_('Pertinence'),
        tracking=True,
        help=_('Select the appropriate pertinence for each matrix line.')
    )
    motivation_id = fields.Many2one(
        'digiit.ebios_rm.motivation',
        string=_('Motivation'),
        tracking=True,
        required=True,
    )


    def _get_pertinence_mapping(self):
        """
        Define the mapping based on the image table:
        Motivation -> Ressource -> Pertinence
        """
        return {
            # Motivation Faible (1)
            (1, 1): 1,  # Faible + Faibles = Faible
            (1, 2): 2,  # Faible + Significatives = Modérée
            (1, 3): 2,  # Faible + Élevées = Modérée

            # Motivation Significative (2)
            (2, 1): 1,  # Significative + Faibles = Faible
            (2, 2): 2,  # Significative + Significatives = Modérée
            (2, 3): 3,  # Significative + Élevées = Élevée

            # Motivation Élevé (3)
            (3, 1): 2,  # Élevé + Faibles = Modérée
            (3, 2): 3,  # Élevé + Significatives = Élevée
            (3, 3): 3,  # Élevé + Élevées = Élevée
        }


    def _auto_set_pertinence(self):
        """Automatically set pertinence based on motivation and ressource"""
        if self.motivation_id and self.ressource_id:
            mapping = self._get_pertinence_mapping()
            key = (self.motivation_id.number, self.ressource_id.number)

            if key in mapping:
                pertinence_number = mapping[key]
                pertinence = self.env['digiit.ebios_rm.pertinence'].search([
                    ('number', '=', pertinence_number)
                ], limit=1)

                if pertinence:
                    self.pertinence_id = pertinence.id


    @api.model_create_multi
    def create(self, vals_list):
        records = super(PertinenceMatrix, self).create(vals_list)
        for record in records:
            record._auto_set_pertinence()
        return records


    def write(self, vals):
        result = super(PertinenceMatrix, self).write(vals)
        if 'motivation_id' in vals or 'ressource_id' in vals:
            for record in self:
                record._auto_set_pertinence()
        return result
