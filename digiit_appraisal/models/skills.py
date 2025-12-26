from odoo import models, fields, api

class SkillType(models.Model):
    _name = "digiit.model.skill_type"
    _description = "Type de compétence"
    _order = "sequence, name"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char("Types de compétences", required=True, tracking=True)
    color = fields.Integer("Couleur", tracking=True)
    sequence = fields.Integer("Ordre", default=1, tracking=True)

    tag_ids = fields.One2many("digiit.model.skill_tag", "type_id", string="Compétences", tracking=True)
    level_ids = fields.One2many("digiit.model.skill_level", "type_id", string="Niveaux", tracking=True)

class SkillTag(models.Model):
    _name = "digiit.model.skill_tag"
    _description = "Compétence"

    name = fields.Char("Compétence", required=True)
    type_id = fields.Many2one("digiit.model.skill_type", required=True, ondelete="cascade")
    color = fields.Integer(related="type_id.color", store=False)
    sequence = fields.Integer("Ordre", default=1)

class SkillLevel(models.Model):
    _name = "digiit.model.skill_level"
    _description = "Niveau de compétence"
    _order = "sequence"

    name = fields.Char("Niveau", required=True)
    type_id = fields.Many2one("digiit.model.skill_type", required=True, ondelete="cascade")
    progression = fields.Integer("En cours (%)", default=0)
    is_default = fields.Boolean("Niveau par défaut", default=False)
    sequence = fields.Integer("Ordre", default=1)

    @api.onchange('is_default')
    def _onchange_is_default(self):
        if self.is_default and self.type_id:
            for level in self.type_id.level_ids:
                if level != self:
                    level.is_default = False

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.is_default and rec.type_id:
                others = self.search([
                    ('type_id', '=', rec.type_id.id),
                    ('id', '!=', rec.id)
                ])
                others.write({'is_default': False})
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'is_default' in vals and vals['is_default']:
            for rec in self:
                if rec.is_default and rec.type_id:
                    others = self.search([
                        ('type_id', '=', rec.type_id.id),
                        ('id', 'not in', rec.ids)
                    ])
                    others.write({'is_default': False})
        return res

class AppraisalSkill(models.Model):
    _name = "digiit.model.appraisal.skill"
    _description = "Compétence de l'évaluation"

    appraisal_id = fields.Many2one("digiit.model.appraisal", required=True, ondelete="cascade")
    type_id = fields.Many2one("digiit.model.skill_type", string='Type', required=True, ondelete="cascade")
    tag_id = fields.Many2one("digiit.model.skill_tag", string='Compétence', required=True, ondelete="cascade")
    level_id = fields.Many2one("digiit.model.skill_level", string="Niveau", ondelete="cascade")
    progression = fields.Integer("En cours (%)", related="level_id.progression", store=True)
    justification = fields.Text("Justificatif")
    sequence = fields.Integer()

    _sql_constraints = [
        ('unique_appraisal_type_tag',
         'unique(appraisal_id, type_id, tag_id)',
         'Cette combinaison de type et tag existe déjà pour cette évaluation!')
    ]
