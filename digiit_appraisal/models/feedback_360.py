from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError
from odoo import http
from odoo.http import request
from odoo.addons.survey.controllers.main import Survey

class Feedback360Participant(models.Model):
    _name = "digiit.model.feedback360.participant"
    _description = "Participant au feedback 360"
    _rec_name = 'display_name'

    appraisal_id = fields.Many2one(
        'digiit.model.appraisal',
        string="Évaluation",
        required=True,
        ondelete='cascade'
    )
    partner_id = fields.Many2one(
        'res.partner',
        string="Contact",
        required=True,
        help="Contact externe (client, fournisseur, etc.)"
    )
    email = fields.Char(
        string="Email",
        compute="_compute_email",
        store=True,
        help="Email du participant"
    )

    # Track invitation and user input
    invitation_sent = fields.Boolean("Invitation envoyée", default=False, tracking=True)
    invitation_date = fields.Datetime("Date d'invitation", readonly=True)

    # REMOVED: user_input_id field to ensure complete anonymity
    # The link between participant and their response is removed

    # No response tracking fields - complete anonymity


    # Add display name for better UX
    display_name = fields.Char("Nom d'affichage", compute="_compute_display_name", store=True)

    @api.depends('partner_id')
    def _compute_email(self):
        for record in self:
            record.email = record.partner_id.email if record.partner_id else False

    @api.depends('partner_id')
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.partner_id.name if record.partner_id else "Nouveau participant"

    @api.constrains('partner_id', 'email')
    def _check_participant_data(self):
        for record in self:
            if not record.partner_id:
                raise ValidationError("Vous devez sélectionner un contact.")
            if not record.email:
                raise ValidationError("L'email du participant est requis.")

    def action_send_invitation(self):
        """Envoie l'invitation de feedback 360 en utilisant le système natif d'Odoo"""
        for record in self:
            survey = record.appraisal_id.feedback360_survey_id
            if not survey:
                raise UserError("Aucune enquête configurée pour cette évaluation.")

            # Create completely anonymous user input - NO partner link
            user_input = survey._create_answer(
                partner=None,  # NO partner link for anonymity
                email=None,  # NO email stored for anonymity
                check_attempts=False  # Don't check attempts by partner
            )

            if user_input:
                # Send custom invitation email with anonymous link
                record._send_anonymous_invitation_email(user_input)

                # Update participant record (but don't link to user_input)
                record.write({
                    'invitation_sent': True,
                    'invitation_date': fields.Datetime.now(),
                    # NO user_input_id stored for anonymity
                })

    def _send_anonymous_invitation_email(self, user_input):
        """Send completely anonymous invitation email"""
        self.ensure_one()

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        # Use anonymous access token only - no participant identification
        survey_url = f"{base_url}/survey/start/{self.appraisal_id.feedback360_survey_id.access_token}?answer_token={user_input.access_token}"

        body_html = f"""
        <p>Bonjour,</p>
        <p>Vous êtes invité(e) à participer à un feedback 360 pour <strong>{self.appraisal_id.employee_id.name}</strong>.</p>
        <p><a href="{survey_url}" style="background-color: #875A7B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Participer au feedback</a></p>
        <p><strong>Important - Anonymat garanti :</strong></p>
        <ul>
            <li>Ce feedback est complètement <strong>anonyme</strong></li>
            <li>Aucune connexion n'est requise</li>
            <li>Vos réponses ne peuvent pas être reliées à votre identité</li>
            <li>Aucun lien n'est conservé entre vous et vos réponses</li>
            <li>Une seule réponse est autorisée par lien</li>
        </ul>
        <p>Cordialement,<br/>L'équipe RH</p>
        """

        # Create and send email
        mail_values = {
            'email_to': self.email,
            'email_from': self.env.company.email or self.env.user.email,
            'subject': f'Invitation feedback 360 - {self.appraisal_id.employee_id.name}',
            'body_html': body_html,
            'auto_delete': True,
        }

        mail = self.env['mail.mail'].create(mail_values)
        mail.send()

class AppraisalFeedback360(models.Model):
    """Extension du modèle Appraisal pour le feedback 360"""
    _inherit = 'digiit.model.appraisal'

    feedback360_survey_template_id = fields.Many2one(
        'survey.survey',
        string="Modèle d'enquête feedback 360",
        domain=[('is_feedback360_template', '=', True)],
        tracking=True
    )
    feedback360_survey_id = fields.Many2one(
        'survey.survey',
        string="Enquête feedback 360",
        readonly=True,
        tracking=True
    )
    feedback360_participant_ids = fields.One2many(
        'digiit.model.feedback360.participant',
        'appraisal_id',
        string="Participants feedback 360"
    )

    # Statistics - based on total survey responses only
    feedback360_invitations_sent = fields.Integer(
        "Invitations envoyées",
        compute="_compute_feedback360_stats"
    )
    feedback360_responses_received = fields.Integer(
        "Réponses totales reçues",
        compute="_compute_feedback360_stats"
    )

    def action_result_survey(self):
        """Show survey results like the native survey 'Show Results' button"""
        self.ensure_one()

        if not self.feedback360_survey_id:
            raise UserError("Aucune enquête créée pour cette évaluation.")

        # Call the survey's native result action
        return self.feedback360_survey_id.action_result_survey()

    @api.depends('feedback360_participant_ids.invitation_sent', 'feedback360_survey_id')
    def _compute_feedback360_stats(self):
        for record in self:
            participants = record.feedback360_participant_ids

            # Count invitations sent
            invitations_sent = len(participants.filtered('invitation_sent'))

            # Count total responses from survey (anonymous)
            responses_received = 0
            if record.feedback360_survey_id:
                responses_received = self.env['survey.user_input'].search_count([
                    ('survey_id', '=', record.feedback360_survey_id.id),
                    ('state', '=', 'done')
                ])

            record.feedback360_invitations_sent = invitations_sent
            record.feedback360_responses_received = responses_received

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.feedback360_survey_template_id:
                record._create_survey_from_template()
        return records

    def write(self, vals):
        for record in self:
            # If the template is being removed or changed, delete old survey
            if 'feedback360_survey_template_id' in vals:
                if not vals['feedback360_survey_template_id'] and record.feedback360_survey_id:
                    record.feedback360_survey_id.unlink()
                elif vals['feedback360_survey_template_id'] != record.feedback360_survey_template_id.id:
                    if record.feedback360_survey_id:
                        record.feedback360_survey_id.unlink()

        result = super().write(vals)

        # Recreate survey if a new template is set
        for record in self:
            if record.feedback360_survey_template_id and not record.feedback360_survey_id:
                record._create_survey_from_template()

        return result

    def _create_survey_from_template(self):
        """Crée une enquête à partir du template"""
        self.ensure_one()

        if not self.feedback360_survey_template_id:
            return

        # Delete existing survey if any
        if self.feedback360_survey_id:
            self.feedback360_survey_id.unlink()

        template = self.feedback360_survey_template_id
        survey_title = f"{template.title} - {self.employee_id.name} - {(self.date_evaluation or fields.Date.today()).strftime('%d/%m/%Y')}"

        # Copy template with proper settings for complete anonymity
        survey_copy = template.copy({
            'title': survey_title,
            'description': f"Feedback 360 pour {self.employee_id.name} (Anonyme)",
            'is_feedback360_template': False,
            'is_feedback360': True,
            'access_mode': 'public',  # Public access for anonymity
            'users_login_required': False,  # No login required
            'users_can_signup': False,  # No signup to avoid tracking
            'is_time_limited': False,  # No time pressure
            'is_attempts_limited': True,
            'attempts_limit': 1,
            'feedback360_appraisal_id': self.id,
            'scoring_type': 'no_scoring',
            'certification': False,
        })
        survey_copy.write({'title': survey_title})

        self.feedback360_survey_id = survey_copy

    def action_send_all_feedback360_invitations(self):
        """Envoie toutes les invitations"""
        if not self.feedback360_survey_id and self.feedback360_survey_template_id:
            self._create_survey_from_template()

        participants_to_send = self.feedback360_participant_ids.filtered(
            lambda p: not p.invitation_sent
        )

        if not participants_to_send:
            raise UserError("Aucun participant en attente d'invitation.")

        participants_to_send.action_send_invitation()

        return

    def action_view_feedback360_results(self):
        """Voir les résultats anonymes"""
        self.ensure_one()

        if not self.feedback360_survey_id:
            raise UserError("Aucune enquête créée pour cette évaluation.")

        # Show ALL user inputs for this survey (completely anonymous)
        user_inputs = self.env['survey.user_input'].search([
            ('survey_id', '=', self.feedback360_survey_id.id),
            ('state', '=', 'done')
        ])

        if not user_inputs:
            raise UserError("Aucune réponse complétée pour le moment.")

        return {
            'type': 'ir.actions.act_window',
            'name': f'Résultats Feedback 360 Anonymes - {self.employee_id.name}',
            'res_model': 'survey.user_input',
            'view_mode': 'list,form',
            'domain': [('id', 'in', user_inputs.ids)],
            'context': {
                'create': False,
                'edit': False,
                'delete': False,
                'search_default_group_by_nothing': 1,  # Don't group by partner
            }
        }

    def action_view_feedback360_survey(self):
        """Voir l'enquête feedback 360"""
        self.ensure_one()

        if not self.feedback360_survey_id:
            raise UserError("Aucune enquête créée pour cette évaluation.")

        return {
            'type': 'ir.actions.act_window',
            'name': f'Enquête Feedback 360 - {self.employee_id.name}',
            'res_model': 'survey.survey',
            'res_id': self.feedback360_survey_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def unlink(self):
        """Supprime les enquêtes associées"""
        surveys_to_delete = self.mapped('feedback360_survey_id').filtered(
            lambda s: not s.is_feedback360_template
        )
        result = super().unlink()
        surveys_to_delete.unlink()
        return result

    # No cron job needed - statistics are computed directly from survey responses


class SurveyFeedback360(models.Model):
    """Extension minimale du modèle Survey"""
    _inherit = 'survey.survey'

    is_feedback360 = fields.Boolean("Enquête feedback 360", default=False)
    is_feedback360_template = fields.Boolean("Modèle feedback 360", default=False)
    feedback360_appraisal_id = fields.Many2one(
        'digiit.model.appraisal',
        string="Évaluation associée"
    )

    def copy(self, default=None):
        """Override copy pour les templates"""
        if default is None:
            default = {}

        if self.is_feedback360_template:
            default.update({
                'is_feedback360_template': False,
                'is_feedback360': True,
                'access_mode': 'public',  # Ensure public access
                'users_login_required': False,
                'users_can_signup': False,  # Prevent signup tracking
                'is_attempts_limited': True,
                'attempts_limit': 1,
                'scoring_type': 'no_scoring',
                'certification': False,
            })

        return super().copy(default)

    def _create_answer(self, partner=None, email=None, check_attempts=True, **additional_vals):
        """Override to ensure complete anonymity for feedback360 surveys"""
        if self.is_feedback360:
            # Force anonymity for feedback360 surveys
            partner = None
            email = None
            check_attempts = False

        return super()._create_answer(
            partner=partner,
            email=email,
            check_attempts=check_attempts,
            **additional_vals
        )


class Feedback360Survey(Survey):
    """Override survey controller to disable partner checking for feedback 360 surveys"""

    def _get_access_data(self, survey_token, answer_token, ensure_token=True, check_partner=True):
        """Override to disable partner checking for feedback 360 surveys"""

        # First, check if this is a feedback 360 survey
        survey_sudo = request.env['survey.survey'].with_context(active_test=False).sudo().search([
            ('access_token', '=', survey_token)
        ], limit=1)

        # If it's a feedback 360 survey, disable partner checking
        if survey_sudo.exists() and survey_sudo.is_feedback360:
            check_partner = False

        # Call parent method with potentially modified check_partner
        return super()._get_access_data(survey_token, answer_token, ensure_token=ensure_token,
                                        check_partner=check_partner)

