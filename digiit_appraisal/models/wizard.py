from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AppraisalConfirmWizard(models.TransientModel):
    _name = 'appraisal.confirm.wizard'
    _description = 'Wizard pour confirmer l\'évaluation'

    appraisal_id = fields.Many2one('digiit.model.appraisal', required=True)
    project_id = fields.Many2one('project.project', string="Projet", required=True)
    task_name = fields.Char(
        string="Nom de la tâche",
        required=True,
    )
    scheduled_date = fields.Date(
        string="Date prévue",
        required=True,
        readonly=True
    )

    @api.model
    def default_get(self, fields_list):
        """Set default values when wizard is opened"""
        res = super().default_get(fields_list)

        # Get appraisal_id from context
        appraisal_id = self._context.get('default_appraisal_id')
        if appraisal_id:
            appraisal = self.env['digiit.model.appraisal'].browse(appraisal_id)
            if appraisal.date_evaluation:
                # Set scheduled_date
                res['scheduled_date'] = appraisal.date_evaluation

                # Set task_name
                date_str = appraisal.date_evaluation.strftime('%d/%m/%Y')
                employee_name = appraisal.employee_id.name
                res['task_name'] = f'Évaluation - {employee_name} - {date_str}'
            else:
                raise UserError(_("Impossible de planifier une évaluation: aucune date trouvée."))

        return res

    def action_confirm_wizard(self):
        # Convert date to datetime with 16:00 time for task creation
        self.appraisal_id.confirm_with_task(self.project_id.id, self.task_name)
        return {'type': 'ir.actions.act_window_close'}


class RequestEvaluationWizard(models.TransientModel):
    _name = 'request.evaluation.wizard'
    _description = 'Request Evaluation Wizard'

    manager_name = fields.Char(string="Manager", readonly=True)
    manager_email = fields.Char(string="Manager Email", readonly=True)
    employee_name = fields.Char(string="Employee", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        employee = self.env['hr.employee'].search([('user_id', '=', self.env.user.id)], limit=1)
        if employee and employee.parent_id:
            res.update({
                'manager_name': employee.parent_id.name,
                'manager_email': employee.parent_id.work_email,
                'employee_name': employee.name,
            })
        return res

    def action_send_email(self):
        if not self.manager_email:
            raise UserError(_("Votre manager n'a pas d'email configuré."))

        email_subject = f"Demande d'évaluation de {self.employee_name}"
        email_body = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px;">
                <p>Bonjour {self.manager_name},</p>

                <p>L\'employé(e) <b>{self.employee_name}</b> souhaite initier une <b>évaluation</b>.</p>

                <p>Merci de bien vouloir prendre les dispositions nécessaires.</p>

                <p style="margin-top: 20px;">
                    Cordialement,<br/>
                    <strong>{self.env.company.name}</strong>
                </p>
            </div>
        """

        self.env['mail.mail'].sudo().create({
            'subject': email_subject,
            'body_html': email_body,
            'email_to': self.manager_email,
            'email_from': self.env.company.email,
        }).send()

        return {'type': 'ir.actions.act_window_close'}
