from odoo import models, fields, api


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # Computed fields for evaluation statistics
    evaluations_completed = fields.Integer(
        string="Évaluations terminées",
        compute="_compute_evaluation_stats",
        store=True
    )
    evaluations_planned = fields.Integer(
        string="Évaluations prévues",
        compute="_compute_evaluation_stats",
        store=True
    )
    evaluations_stats= fields.Char("Évaluations stats", computed="_compute_evaluation_stats", store=True, readonly=True)
    @api.depends('appraisal_ids.status')
    def _compute_evaluation_stats(self):
        for employee in self:
            appraisals = self.env['digiit.model.appraisal'].search([
                ('employee_id', '=', employee.id)
            ])
            completed= len(appraisals.filtered(lambda a: a.status == 'done'))
            planned=len(appraisals.filtered(lambda a: a.status in ('draft', 'confirmed')))
            employee.evaluations_completed = completed
            employee.evaluations_planned = planned
            employee.evaluations_stats=f"( {completed} réalisées / {planned} planifiées )"
    # Relation to appraisals
    appraisal_ids = fields.One2many('digiit.model.appraisal', 'employee_id', string="Évaluations")

    def action_create_evaluation(self):
        """Action to create a new evaluation for this employee"""
        return {
            'type': 'ir.actions.act_window',
            'name': f'Nouvelle évaluation - {self.name}',
            'res_model': 'digiit.model.appraisal',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_employee_id': self.id,
                'form_view_initial_mode': 'edit',
            }
        }

    def action_view_evaluations(self):
        """Action to view all evaluations for this employee"""
        return {
            'type': 'ir.actions.act_window',
            'name': f'Évaluations - {self.name}',
            'res_model': 'digiit.model.appraisal',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {
                'default_employee_id': self.id,
                'search_default_group_by_status': 1,
            }
        }