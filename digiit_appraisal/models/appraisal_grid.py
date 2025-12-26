from odoo import models, fields

class AppraisalGrid(models.Model):
    _name = "digiit.model.appraisal_grid"
    _description = "Grille d'évaluation"
    _order = "sequence"

    name = fields.Char(string="Nom", required=True)
    sequence = fields.Integer(string="Ordre", default=10)
