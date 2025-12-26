from odoo import models, fields, api
from odoo.exceptions import ValidationError


class AppraisalSettings(models.Model):
    _name = 'digiit.model.appraisal.settings'
    _description = "Paramètres des évaluations"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # This is the key to making it a singleton
    _rec_name = 'id'

    default_template_id = fields.Many2one(
        'digiit.model.appraisal_template',
        string="Modèle par défaut", 
        tracking=True,
        domain="[('company_id', 'in', [company_id, False])]"
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        domain="[('id', 'in', allowed_company_ids)]"
    )

    @api.constrains('company_id')
    def _check_company_uniqueness(self):
        """Ensure only one settings record per company and only one without company."""
        for record in self:
            if record.company_id:
                # Check for duplicate settings per company
                other_settings = self.search([
                    ('company_id', '=', record.company_id.id),
                    ('id', '!=', record.id)
                ])
                if other_settings:
                    raise ValidationError(f"Il ne peut y avoir qu'un seul enregistrement de paramètres par société.")
            else:
                # Check for duplicate settings without company
                other_settings = self.search([
                    ('company_id', '=', False),
                    ('id', '!=', record.id)
                ])
                if other_settings:
                    raise ValidationError(f"Il ne peut y avoir qu'un seul enregistrement de paramètres global.")

    @api.model
    def get_settings(self, company_id=None):
        """Get or create the settings record for the specified company."""
        
        # First try to get company-specific settings
        settings = self.search([('company_id', '=', company_id)], limit=1)
        if not settings:
            # If no company-specific settings, try to get global settings
            settings = self.search([('company_id', '=', False)], limit=1)
            new_setting = self.create({'company_id': company_id})
            if not settings:
                settings = new_setting
        return settings

        
    def _compute_display_name(self):
        for record in self:
            if record.company_id:
                record.display_name = f"Paramètres - {record.company_id.display_name}"
            else:
                record.display_name = "Paramètres (global)"