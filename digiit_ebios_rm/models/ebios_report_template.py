from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class DigiitReportTemplate(models.Model):
    _name = 'digiit.ebios_rm.report.template'
    _description = 'Report Template Storage'

    name = fields.Char(string='Nom du Template', required=True)
    report_type = fields.Selection([
        ('dda_ebios', 'Rapport DdA Excel'),
        ('word_report', 'Rapport Word'),
        ('ppt_report', 'Presentation PPT'),
    ], string='Type de Rapport', required=True)
    template_file = fields.Binary(
        string='Fichier de Template',
        required=True,
        attachment=True,
        help='Importer le fichier modèle Excel (.xlsx, .docx, .pptx)'
    )
    template_filename = fields.Char(string='Nom du Fichier')
    description = fields.Text(string='Description', help='Instructions pour utiliser ce modèle')
    is_default = fields.Boolean(
        string='Template système',
        default=False,
        help='Template système par défaut (non modifiable)'
    )
    use_this_template = fields.Boolean(
        string='Utiliser ce template',
        default=True,
        help='Si activé, ce template sera utilisé pour générer les rapports. Sinon, le template système sera utilisé.'
    )

    @api.constrains('is_default')
    def _check_default_template(self):
        """Ensure only one default template per type"""
        for record in self:
            if record.is_default:
                other_defaults = self.search([
                    ('id', '!=', record.id),
                    ('report_type', '=', record.report_type),
                    ('is_default', '=', True)
                ])
                if other_defaults:
                    raise ValidationError(
                        _("Il existe déjà un template système pour le type '%s'.") % record.report_type
                    )

    @api.constrains('use_this_template', 'report_type', 'is_default')
    def _check_single_active_template(self):
        """Ensure only one custom template per report type has use_this_template=True"""
        for record in self:
            if record.use_this_template and not record.is_default:
                other_active = self.search([
                    ('id', '!=', record.id),
                    ('report_type', '=', record.report_type),
                    ('is_default', '=', False),
                    ('use_this_template', '=', True)
                ])
                if other_active:
                    raise ValidationError(
                        _("Un autre template personnalisé pour le type '%s' est déjà activé: '%s'. "
                          "Veuillez le désactiver d'abord.") % (record.report_type, other_active[0].name)
                    )

    def write(self, vals):
        """Prevent modification of default templates"""
        for record in self:
            if record.is_default and not self.env.user.has_group('base.group_system'):
                if any(key in vals for key in ['name', 'report_type', 'template_file', 'is_default']):
                    raise ValidationError(
                        _("Les templates par défaut ne peuvent pas être modifiés. "
                          "Veuillez créer un nouveau template personnalisé.")
                    )
        return super().write(vals)

    def unlink(self):
        """Prevent deletion of default templates"""
        for record in self:
            if record.is_default:
                raise ValidationError(
                    _("Les templates par défaut ne peuvent pas être supprimés.")
                )
        return super().unlink()

    def action_download_template(self):
        """Action to download the template file"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{self._name}/{self.id}/template_file/{self.template_filename}?download=true',
            'target': 'self',
        }

    def action_duplicate_as_custom(self):
        """Duplicate default template as custom template"""
        self.ensure_one()
        if not self.is_default:
            raise ValidationError(_("Cette action est disponible uniquement pour les templates système."))

        new_template = self.copy({
            'name': f"{self.name} (Personnalisé)",
            'is_default': False,
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': new_template.id,
            'view_mode': 'form',
            'target': 'current',
        }