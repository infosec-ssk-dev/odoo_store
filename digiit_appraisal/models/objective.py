from odoo import models, fields, api

class ObjectiveTag(models.Model):
    _name = "digiit.model.objective.tag"
    _description = "Étiquette d'objectif"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char("Nom", required=True, tracking=True)


class AppraisalObjective(models.Model):
    _name = "digiit.model.objective"
    _description = "Objectif d'évaluation"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Objectif", required=True, tracking=True)
    employee_id = fields.Many2one('hr.employee', string="Employé", required=True, tracking=True)
    manager_id = fields.Many2one('hr.employee', string="Manager", compute="_compute_manager", store=True)
    progress = fields.Selection([
        ('0', '0%'),
        ('25', '25%'),
        ('50', '50%'),
        ('75', '75%'),
        ('100', '100%'),
    ], string="En cours", default='0', tracking=True)
    date_deadline = fields.Date(string="Date limite", tracking=True)
    tag_ids = fields.Many2many("digiit.model.objective.tag", string="Étiquettes", tracking=True)
    description = fields.Text(string="Description", tracking=True)
    is_employee = fields.Boolean(compute="_compute_user_roles", store=False)
    @api.depends('employee_id')
    def _compute_user_roles(self):
        current_user = self.env.user
        is_hr = current_user.has_group('hr.group_hr_user')

        for rec in self:
            rec.is_employee = rec.employee_id.user_id == current_user and not is_hr
    @api.depends('employee_id')
    def _compute_manager(self):
        for rec in self:
            rec.manager_id = rec.employee_id.parent_id if rec.employee_id else False

    def action_mark_done(self):
        for rec in self:
            rec.progress = '100'
