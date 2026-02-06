from odoo import models, fields, api, _


class StudyPacs(models.Model):
    _name = 'digiit.ebios_rm.study.pacs'
    _description = 'Plan d\'amélioration continue de la sécurité'
    _rec_name = 'display_name_custom'

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_('Étude'),
        required=True,
        ondelete='cascade',
        index=True
    )

    study_measure_id = fields.Many2one(
        'digiit.ebios_rm.study.measure',
        string=_('Mesure'),
        required=True,
        ondelete='cascade',
        index=True,
        domain="[('study_id', '=', study_id)]"
    )

    measure_title = fields.Char(
        string=_('Titre de la mesure'),
        related='study_measure_id.measure_title',
        readonly=True,
        store=True
    )

    measure_number = fields.Char(
        string=_('Numéro'),
        related='study_measure_id.measure_number',
        readonly=True,
        store=True
    )

    measure_contenu = fields.Text(
        string=_('Contenu'),
        related='study_measure_id.measure_contenu',
        readonly=True
    )

    referential_name = fields.Char(
        string=_('Référentiel'),
        related='study_measure_id.study_chapter_id.study_referential_id.referential_id.name',
        readonly=True,
        store=True
    )

    referential_version = fields.Char(
        string=_('Version'),
        related='study_measure_id.study_chapter_id.study_referential_id.referential_id.version',
        readonly=True,
        store=True
    )

    measure_display = fields.Char(
        string=_('Mesure de sécurité'),
        compute='_compute_measure_display',
        store=True
    )

    system_domain_ids = fields.Many2many(
        'digiit.ebios_rm.system.domain',
        string=_('Domaines de sécurité'),
        compute='_compute_system_domains',
        store=True,
        readonly=True
    )

    risk_treatment_ids = fields.Many2many(
        'digiit.ebios_rm.risk.treatment',
        string=_('Scénarios de risques associés'),
        compute='_compute_risk_treatments',
        store=True
    )

    ecosystem_ids = fields.Many2many(
        'digiit.ebios_rm.ecosystem',
        string=_('PiPs associés'),
        compute='_compute_ecosystems',
        store=True
    )

    security_baseline_applicable = fields.Boolean(
        string=_('Issu du socle de sécurité'),
        compute='_compute_security_baseline_applicable',
        store=True,
        readonly=True
    )

    risk_treatment_count = fields.Integer(
        string=_('Nombre de risques'),
        compute='_compute_risk_treatment_count',
        store=True
    )

    # risk_identifiers = fields.Char(
    #     string=_('Scénarios de risques associés'),
    #     compute='_compute_risk_identifiers',
    #     store=True,
    #     help=_('Liste des risques associés')
    # )

    # pacs specific fields
    echeance = fields.Selection(
        [
            ('court_terme', _('Court terme (< 3 mois)')),
            ('moyen_terme', _('Moyen terme (3-6 mois)')),
            ('long_terme', _('Long terme (> 6 mois)')),
        ],
        string=_('Échéance'),
        tracking=True,
        help=_('Délai prévu pour la mise en œuvre de la mesure')
    )

    cout = fields.Selection(
        [
            ('faible', _('Faible')),
            ('moyen', _('Moyen')),
            ('eleve', _('Élevé')),
            ('tres_eleve', _('Très élevé')),
        ],
        string=_('Coût/ Complexité'),
        tracking=True,
        help=_('Estimation du coût de mise en œuvre')
    )

    freins = fields.Text(
        string=_('Freins et difficultés de mise en œuvre'),
        tracking=True,
        help=_('Obstacles ou difficultés identifiés pour la mise en œuvre')
    )

    plan_action = fields.Text(
        string=_('Plan d\'action'),
        tracking=True,
        help=_('Description détaillée des actions à mettre en œuvre')
    )

    application_status_id = fields.Many2one(
        'digiit.ebios_rm.application.status.scale',
        string=_('Statut'),
        tracking=True
    )

    status_name = fields.Char(
        string=_('Statut'),
        related='application_status_id.name',
        readonly=True
    )

    status_color = fields.Char(
        string=_('Couleur'),
        related='application_status_id.color',
        readonly=True
    )

    # Display name
    display_name_custom = fields.Char(
        string=_("Nom d'affichage"),
        compute="_compute_display_name_custom",
        store=True,
        index=True,
    )

    @api.depends('measure_number', 'measure_title')
    def _compute_measure_display(self):
        """Combine measure number and title"""
        for record in self:
            if record.measure_number and record.measure_title:
                record.measure_display = f"{record.measure_number} - {record.measure_title}"
            elif record.measure_title:
                record.measure_display = record.measure_title
            elif record.measure_number:
                record.measure_display = record.measure_number
            else:
                record.measure_display = ''

    @api.depends('measure_number', 'measure_title')
    def _compute_display_name_custom(self):
        for record in self:
            if record.measure_number and record.measure_title:
                record.display_name_custom = f"{record.measure_number} - {record.measure_title}"
            elif record.measure_title:
                record.display_name_custom = record.measure_title
            else:
                record.display_name_custom = f"PACS {record.id}"

    def name_get(self):
        return [(r.id, r.display_name_custom or f"PACS {r.id}") for r in self]

    @api.depends('study_measure_id', 'study_measure_id.measure_system_domains')
    def _compute_system_domains(self):
        for rec in self:
            rec.system_domain_ids = rec.study_measure_id.measure_system_domains

    @api.depends('study_measure_id')
    def _compute_risk_treatments(self):
        """Find all risk treatments that reference this measure"""
        for record in self:
            if record.study_measure_id:
                risk_treatments = self.env['digiit.ebios_rm.risk.treatment'].search([
                    ('study_id', '=', record.study_id.id),
                    ('equivalent_mesures_referentiel', 'in', [record.study_measure_id.id])
                ])
                record.risk_treatment_ids = risk_treatments
            else:
                record.risk_treatment_ids = False

    @api.depends('risk_treatment_ids')
    def _compute_risk_treatment_count(self):
        for record in self:
            record.risk_treatment_count = len(record.risk_treatment_ids)

    # # NEW: Compute risk identifiers
    # @api.depends('risk_treatment_ids', 'risk_treatment_ids.identifier')
    # def _compute_risk_identifiers(self):
    #     for record in self:
    #         if record.risk_treatment_ids:
    #             identifiers = record.risk_treatment_ids.mapped('identifier')
    #             # Filter out empty values and join
    #             identifiers = [i for i in identifiers if i]
    #             record.risk_identifiers = ', '.join(identifiers) if identifiers else ''
    #         else:
    #             record.risk_identifiers = ''

    @api.depends('study_measure_id', 'study_measure_id.study_question_ids.response_application')
    def _compute_security_baseline_applicable(self):
        """
        Mark as applicable if any question in the measure has response_application = 'non'
        """
        for record in self:
            if record.study_measure_id and record.study_measure_id.study_question_ids:
                # Check if any question has response_application = 'non'
                answered_no_questions = record.study_measure_id.study_question_ids.filtered(
                    lambda q: q.response_application == 'non'
                )
                record.security_baseline_applicable = bool(answered_no_questions)
            else:
                record.security_baseline_applicable = False

    @api.depends('study_measure_id')
    def _compute_ecosystems(self):
        """Find all ecosystems that reference this measure"""
        for record in self:
            if record.study_measure_id:
                ecosystems = self.env['digiit.ebios_rm.ecosystem'].search([
                    ('study_id', '=', record.study_id.id),
                    ('equivalent_mesures_referentiel_to_implement', 'in', [record.study_measure_id.id])
                ])
                record.ecosystem_ids = ecosystems
            else:
                record.ecosystem_ids = False

    def action_view_risk_treatments(self):
        """Open risk treatments associated with this measure"""
        self.ensure_one()
        return {
            'name': _('Risques - %s') % self.measure_title,
            'type': 'ir.actions.act_window',
            'res_model': 'digiit.ebios_rm.risk.treatment',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.risk_treatment_ids.ids)],
            'context': {'default_study_id': self.study_id.id}
        }

    _sql_constraints = [
        ('unique_study_measure_pacs',
         'UNIQUE(study_id, study_measure_id)',
         'Un PACS existe déjà pour cette mesure dans cette étude!')
    ]