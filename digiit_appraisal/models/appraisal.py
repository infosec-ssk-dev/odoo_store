from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from datetime import datetime, time


class Appraisal(models.Model):
    _name = "digiit.model.appraisal"
    _description = "Évaluation"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    employee_id = fields.Many2one(
        'hr.employee',
        string="Employé",
        required=True,
        tracking=True,
        domain="[('company_id', 'in', allowed_company_ids)]"
    )
    manager_id = fields.Many2one(
        'hr.employee',
        string="Manager",
        compute="_compute_manager",
        store=True
    )
    status = fields.Selection([
        ('draft', 'Brouillon'),
        ('confirmed', 'Confirmé'),
        ('done', 'Terminé'),
        ('canceled', 'Annulé'),
    ], string="Statut", default='draft', tracking=True)
    date_evaluation = fields.Date(string="Date d'évaluation", tracking=True)
    template_id = fields.Many2one(
        'digiit.model.appraisal_template',
        string="Modèle d'évaluation",
        compute="_compute_default_template",
        inverse="_inverse_template_id",
        store=True,
        domain="[('company_id', 'in', [company_id, False])]",
        tracking=True
    )

    job_title = fields.Char(string="Poste", compute="_compute_employee_info", store=True)
    department = fields.Char(string="Département", compute="_compute_employee_info", store=True)
    company_id = fields.Many2one('res.company', string="Société", compute="_compute_employee_info", store=True)

    employee_feedback_html = fields.Html("Feedback de l'employé")
    manager_feedback_html = fields.Html("Feedback du manager")
    # New fields for previous and next evaluation dates
    previous_date_evaluation = fields.Date(
        string="Précédente date d'évaluation",
        compute="_compute_previous_date_evaluation",
        store=False
    )
    next_date_evaluation = fields.Date(string="Prochaine date d'évaluation", tracking=True)
    note_finale_id = fields.Many2one(
        'digiit.model.appraisal_grid',
        string="Note finale",
        help="Sélectionnez la note finale issue de la grille d’évaluation", tracking=True
    )
    appraisal_skill_ids = fields.One2many("digiit.model.appraisal.skill", "appraisal_id", string="Compétences",
                                          tracking=True)

    # Visibility control fields
    employee_feedback_visible_manager = fields.Boolean(
        string="visible par le manager avant finalisation",
        default=True, tracking=True
    )
    manager_feedback_visible_employee = fields.Boolean(
        string="visible par l'employé avant finalisation",
        default=True, tracking=True
    )
    show_employee_feedback = fields.Boolean(
        string="Show Employee Feedback",
        compute="_compute_role_based_visibility"
    )
    show_manager_feedback = fields.Boolean(
        string="Show Manager Feedback",
        compute="_compute_role_based_visibility"
    )
    employee_feedback_done = fields.Boolean(
        string="Auto-évaluation terminée",
        default=False,
        help="Indique si l'employé a terminé son auto-évaluation", tracking=True
    )
    manager_feedback_done = fields.Boolean(
        string="Évaluation manager terminée",
        default=False,
        help="Indique si le manager a terminé son évaluation", tracking=True
    )
    note_privee = fields.Html(
        string="Note privée",
        help="Note privée (uniquement accessible aux personnes définies comme responsables)"
    )
    show_private_note = fields.Boolean(
        compute="_compute_show_private_note"
    )

    can_edit_employee_feedback = fields.Boolean(compute="_compute_access_rights", store=False)
    can_edit_manager_feedback = fields.Boolean(compute="_compute_access_rights", store=False)
    can_edit_employee_feedback_done = fields.Boolean(compute="_compute_access_rights", store=False)
    can_edit_manager_feedback_done = fields.Boolean(compute="_compute_access_rights", store=False)
    is_employee = fields.Boolean(compute="_compute_user_roles", store=False)
    is_manager = fields.Boolean(compute="_compute_user_roles", store=False)

    project_id = fields.Many2one('project.project', string="Projet", tracking=True)
    task_id = fields.Many2one('project.task', string="Tâche créée", readonly=True, tracking=True)

    # Python method in your appraisal model
    def action_open_evaluation_task(self):
        """Open the related task"""
        if not self.task_id:
            raise UserError(_("No task is associated with this appraisal."))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Task'),
            'res_model': 'project.task',
            'res_id': self.task_id.id,
            'view_mode': 'form',
            'target': 'current',
            'context': self.env.context,
        }

    def action_confirm(self):
        if self.is_employee:
            raise ValidationError("Vous n'avez pas les droits de changer l'état d'évaluation.")

        # Show wizard to select project and scheduled date
        return {
            'type': 'ir.actions.act_window',
            'name': 'Confirmer l\'évaluation',
            'res_model': 'appraisal.confirm.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_appraisal_id': self.id,
                'default_project_id': self.project_id.id if self.project_id else False,
            }
        }

    def action_send_employee_notification(self):
        """Send notification email to employee about planned evaluation"""
        if not self.employee_id.work_email:
            raise UserError(_("L'employé n'a pas d'email configuré."))

        email_subject = f"Évaluation prévue - {self.employee_id.name}"
        email_body = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px;">
                <p>Bonjour {self.employee_id.name},</p>
                <p>Votre manager <b>{self.manager_id.name}</b> planifie une <b>évaluation</b> pour le <b>{self.date_evaluation.strftime('%d/%m/%Y') if self.date_evaluation else 'date à définir'}</b>.</p>
                <p>Vous recevrez une notification une fois l'évaluation confirmée.</p>
                <p style="margin-top: 20px;">
                    Cordialement,<br/>
                    <strong>{self.env.company.name}</strong>
                </p>
            </div>
        """

        self.env['mail.mail'].sudo().create({
            'subject': email_subject,
            'body_html': email_body,
            'email_to': self.employee_id.work_email,
            'email_from': self.env.company.email,
        }).send()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Email envoyé'),
                'message': _('Notification envoyée à l\'employé'),
                'type': 'success',
            }
        }

    def _send_confirmation_emails(self):
        """Send confirmation emails to both manager and employee"""
        # Email to employee
        if self.employee_id.work_email:
            employee_subject = f"Évaluation confirmée - {self.employee_id.name}"
            employee_body = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px;">
                    <p>Bonjour {self.employee_id.name},</p>
                    <p>Votre évaluation prévue a été <b>confirmée</b> pour le <b>{self.date_evaluation.strftime('%d/%m/%Y')}</b>.</p>
                    <p>Une tâche a été créée et vous pouvez maintenant accéder à votre évaluation dans le système.</p>
                    <p style="margin-top: 20px;">
                        Cordialement,<br/>
                        <strong>{self.env.company.name}</strong>
                    </p>
                </div>
            """

            self.env['mail.mail'].sudo().create({
                'subject': employee_subject,
                'body_html': employee_body,
                'email_to': self.employee_id.work_email,
                'email_from': self.employee_id.company_id.email,
            }).send()

        # Email to manager
        if self.manager_id and self.manager_id.work_email:
            manager_subject = f"Évaluation confirmée - {self.employee_id.name}"
            manager_body = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px;">
                    <p>Bonjour {self.manager_id.name},</p>
                    <p>L'évaluation prévue de <b>{self.employee_id.name}</b> a été confirmée pour le <b>{self.date_evaluation.strftime('%d/%m/%Y')}</b>.</p>
                    <p>Une tâche a été créée et vous pouvez maintenant procéder à l'évaluation.</p>
                    <p style="margin-top: 20px;">
                        Cordialement,<br/>
                        <strong>{self.env.company.name}</strong>
                    </p>
                </div>
            """

            self.env['mail.mail'].sudo().create({
                'subject': manager_subject,
                'body_html': manager_body,
                'email_to': self.manager_id.work_email,
                'email_from': self.employee_id.company_id.email,
            }).send()

    def confirm_with_task(self, project_id, task_name):
        """Confirm appraisal and create task"""
        self.project_id = project_id
        self.status = 'confirmed'
        scheduled_datetime = datetime.combine(self.date_evaluation, time(16, 0))

        # Create task with 2x30min duration
        task_vals = {
            'name': task_name,
            'project_id': project_id,
            'date_deadline': scheduled_datetime,
            'allocated_hours': 1.0,
            'user_ids': [(6, 0, self._get_assignees())],
        }

        task = self.env['project.task'].create(task_vals)
        self.task_id = task.id
        # Send automatic emails
        self._send_confirmation_emails()

        return True

    def _get_assignees(self):
        """Get list of user IDs to assign to the task"""
        assignees = []
        if self.employee_id.user_id:
            assignees.append(self.employee_id.user_id.id)
        if self.manager_id and self.manager_id.user_id:
            assignees.append(self.manager_id.user_id.id)
        return assignees

    @api.onchange('employee_id')
    def _onchange_employee_id_check_manager_and_user(self):
        for record in self:
            employee = record.employee_id
            current_user = self.env.user

            if not employee:
                return

            # Case 1: Employee has no manager
            if not employee.parent_id:
                raise ValidationError(_("Cet employé n'a pas de manager assigné."))

            # Case 2: Employee is the current user
            if employee.user_id == current_user:
                raise UserError(_(
                    "Vous ne pouvez pas créer une évaluation pour vous-même.\n"
                    "Veuillez utiliser le bouton 'Demander évaluation' pour avertir votre manager."
                ))

    def action_request_evaluation_wizard(self):
        # For list view buttons, self can be a recordset or empty
        employee = self.env['hr.employee'].search([('user_id', '=', self.env.user.id)], limit=1)
        if not employee or not employee.parent_id:
            raise UserError(_("Vous n'avez pas de manager assigné."))

        return {
            'type': 'ir.actions.act_window',
            'name': 'Demander une évaluation',
            'res_model': 'request.evaluation.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    @api.depends('employee_id')
    def _compute_show_private_note(self):
        for rec in self:
            user = self.env.user
            is_manager = rec.is_manager
            is_admin = user.has_group('base.group_system')
            rec.show_private_note = is_manager or is_admin

    @api.depends('employee_id')
    def _compute_user_roles(self):
        current_user = self.env.user
        for rec in self:
            rec.is_employee = rec.employee_id.user_id == current_user
            rec.is_manager = rec.employee_id.parent_id.user_id == current_user if rec.employee_id.parent_id else False

    @api.depends('employee_id', 'status')
    def _compute_access_rights(self):
        for rec in self:
            rec.can_edit_employee_feedback = ((not rec.is_employee) and rec.status == 'draft') or (
                    rec.is_employee and rec.status == 'confirmed')
            rec.can_edit_manager_feedback = ((not rec.is_employee) and rec.status == 'draft') or (
                    rec.is_manager and rec.status == 'confirmed')
            rec.can_edit_employee_feedback_done = rec.is_employee and rec.status == 'confirmed'
            rec.can_edit_manager_feedback_done = rec.is_manager and rec.status == 'confirmed'

    @api.depends('employee_id')
    def _compute_employee_info(self):
        for rec in self:
            rec.job_title = rec.employee_id.job_title or ''
            rec.department = rec.employee_id.department_id.name if rec.employee_id.department_id else ''
            rec.company_id = rec.employee_id.company_id

    @api.depends('employee_id')
    def _compute_manager(self):
        for rec in self:
            rec.manager_id = rec.employee_id.parent_id if rec.employee_id else False

    @api.depends('employee_id', 'employee_feedback_visible_manager', 'manager_feedback_visible_employee', 'status')
    def _compute_role_based_visibility(self):
        for record in self:
            if record.is_employee:
                # Employee always sees their own feedback
                record.show_employee_feedback = True
                if record.manager_feedback_visible_employee:
                    record.show_manager_feedback = True
                else:
                    record.show_manager_feedback = record.status in ('draft', 'done')
            else:
                record.show_manager_feedback = True
                if record.employee_feedback_visible_manager:
                    record.show_employee_feedback = True
                else:
                    record.show_employee_feedback = record.status in ('draft', 'done')

    @api.depends('employee_id', 'date_evaluation')
    def _compute_previous_date_evaluation(self):
        for rec in self:
            if rec.employee_id and rec.date_evaluation:
                # Find the most recent evaluation for this employee before current date
                previous_appraisal = self.search([
                    ('employee_id', '=', rec.employee_id.id),
                    ('date_evaluation', '<', rec.date_evaluation),
                    ('id', '!=', rec.id),
                    ('status', '=', 'done')
                ], order='date_evaluation desc', limit=1)
                rec.previous_date_evaluation = previous_appraisal.date_evaluation if previous_appraisal else False
            else:
                rec.previous_date_evaluation = False

    @api.depends('company_id')
    def _compute_default_template(self):
        """Compute default template based on company settings"""
        for rec in self:
            if rec.company_id:
                # Only set default if no template is set or current template is from different company
                should_set_default = not rec.template_id
                
                if should_set_default:
                    settings = self.env['digiit.model.appraisal.settings'].get_settings(rec.company_id.id)
                    rec.template_id = settings.default_template_id if settings.default_template_id else False
            else:
                rec.template_id = False

    def _inverse_template_id(self):
        """Allow manual editing of template_id"""
        # This method allows the field to be editable
        # The value is already set by the user, so we don't need to do anything
        pass

    @api.onchange('date_evaluation')
    def _onchange_date_evaluation(self):
        """Reset next_date_evaluation if it becomes invalid"""
        if self.date_evaluation and self.next_date_evaluation:
            if self.next_date_evaluation <= self.date_evaluation:
                self.next_date_evaluation = False

    @api.constrains('date_evaluation', 'next_date_evaluation')
    def _check_evaluation_dates(self):
        for record in self:
            if record.date_evaluation and record.next_date_evaluation:
                if record.next_date_evaluation <= record.date_evaluation:
                    raise ValidationError(
                        "La prochaine date d'évaluation doit être postérieure à la date d'évaluation actuelle."
                    )

    @api.onchange('template_id')
    def _onchange_template_id(self):
        if self.status == 'draft':
            if self.template_id:
                self.employee_feedback_html = self.template_id.employee_html
                self.manager_feedback_html = self.template_id.manager_html
            else:
                self.employee_feedback_html = None
                self.manager_feedback_html = None

    def action_done(self):
        if self.is_employee:
            raise ValidationError("Vous n'avez pas les droits de changer l'état d'évaluation.")
        elif not self.employee_feedback_done or not self.manager_feedback_done:
            raise ValidationError(
                "L'auto-évaluation de l'employé et L'évaluation du manager doivent être les deux terminées.")
        elif not self.note_finale_id:
            raise ValidationError("Veuillez sélectionner une note finale pour l'évaluation.")
        else:
            self.status = 'done'

    def action_cancel(self):
        if self.is_employee:
            raise ValidationError("Vous n'avez pas les droits de changer l'état d'évaluation.")
        else:
            self.status = 'canceled'

    def action_reopen(self):
        if self.is_employee:
            raise ValidationError("Vous n'avez pas les droits de changer l'état d'évaluation.")
        else:
            if self.template_id:
                self.employee_feedback_html = self.template_id.employee_html
                self.manager_feedback_html = self.template_id.manager_html
            else:
                self.employee_feedback_html = None
                self.manager_feedback_html = None
            self.status = 'draft'

    @api.model_create_multi
    def create(self, vals_list):
        user = self.env.user
        for vals in vals_list:
            employee = self.env['hr.employee'].browse(vals.get('employee_id'))
            is_manager = employee.parent_id and employee.parent_id.user_id == user
            is_hr = user.has_group('hr.group_hr_user')

            if not (is_hr or is_manager):
                raise ValidationError("Vous n'avez pas les droits pour créer une évaluation pour cet employé.")

        return super().create(vals_list)

    def unlink(self):
        for record in self:
            user = self.env.user
            is_creator = record.create_uid == user
            is_hr = user.has_group('hr.group_hr_user')

            if not (is_creator or is_hr):
                raise ValidationError("Vous ne pouvez supprimer que les évaluations créés par vous.")
        return super().unlink()

    def _compute_display_name(self):
        for record in self:
            if record.employee_id:
                record.display_name = f"{record.employee_id.name}"

    def action_view_appraisal_skills(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Compétences',
            'res_model': 'digiit.model.appraisal.skill',
            'view_mode': 'list,form',
            'target': 'current',
            'domain': [('appraisal_id', '=', self.id)],
            'context': {'default_appraisal_id': self.id},
        }

    def action_view_employee_objectives(self):
        """Action to view objectives for the current employee"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Objectifs de {self.employee_id.name}',
            'res_model': 'digiit.model.objective',
            'view_mode': 'list,form',
            'target': 'current',
            'domain': [('employee_id', '=', self.employee_id.id)],
            'context': {
                'default_employee_id': self.employee_id.id,
                'search_default_in_progress': True,
            },
        }
