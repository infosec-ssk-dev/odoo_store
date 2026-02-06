from odoo import models, fields, _, api


class DreadedEvent(models.Model):
    _name = 'digiit.ebios_rm.dreaded.event'
    _inherit = ['digiit.ebios_rm.incremental.identifier']

    name = fields.Char(
        string=_('Nom'),
        tracking=True,
        compute="_compute_name",
        store=True)
    title = fields.Text(
        string=_('Événement redouté intitulé'),
        tracking=True,
        required=True,
    )

    impacted_security_criteria = fields.Many2many(
        'digiit.ebios_rm.security.criteria',
        'dreaded_event_security_rel',
        'dreaded_event_id',
        'security_criteria_id',
        string=_('Critères de sécurité impactés'),
        tracking=True,
        required=True,
    )

    brand_image_impact = fields.Selection(
        [
            ('1', _("1")),
            ('2', _("2")),
            ('3', _("3")),
            ('4', _("4")),
        ],
        string=_('Impact sur l\'image de marque'),
        tracking=True,
        required=True,
        help=_("""
    1 - Faible nuisance____________________
    2 - Nuisance significative et dégradation de l'image en interne____________________
    3 - Nuisance grave. Dégradation auprès de tiers et de clients____________________
    4 - Perte de confiance dans l'entreprise et/ou perte d'un grand nombre de clients et/ou usagers""")
    )

    impact_justification = fields.Text(
        string=_('Justification des Impacts')
    )

    financial_impact = fields.Selection(
        [
            ('1', _("1")),
            ('2', _("2")),
            ('3', _("3")),
            ('4', _("4")),
        ],
        string=_('Impact financier'),
        tracking=True,
        required=True,
        help=_("""
    1 - Pertes financières faibles____________________
    2 - Pertes financières d'importance modérée____________________
    3 - Pertes financières liées à des affaires d'importance considérable et/ou dégâts conséquents nécessitant des dépenses relativement importantes____________________
    4 - Pertes financières liées à des affaires très importantes et/ou dégâts très importants nécessitant des investissements importants""")
    )
    legal_impact = fields.Selection(
        [
            ('1', _("1")),
            ('2', _("2")),
            ('3', _("3")),
            ('4', _("4")),
        ],
        string=_('Impact juridique'),
        tracking=True,
        required=True,
        help=_("""
    1 - Faible impact juridique / administratif____________________
    2 - Non-respect de la réglementation comptable, fiscale, juridique ou des exigences contractuelles entraînant un simple rappel à l'ordre ou mise en demeure____________________
    3 - Impact juridique significatif____________________
    4 - Sanction judiciaire ou administrative""")
    )
    operational_impact = fields.Selection(
        [
            ('1', _("1")),
            ('2', _("2")),
            ('3', _("3")),
            ('4', _("4")),
        ],
        string=_('Impact opérationnel'),
        tracking=True,
        required=True,
        help=_("""
    1 - Faible impact interne sur un seul service____________________
    2 - Faible impact en interne sur plusieurs services____________________
    3 - Dégradation significative d'un ou de plusieurs services critiques____________________
    4 - Forte dégradation / Arrêt d'un ou de plusieurs services critiques""")
    )
    gross_gravity = fields.Char(

        string=_('Gravité brutes'),
        compute='_compute_gross_gravity',
        store=True,
        tracking=True,
    )
    existant_safety_measures = fields.Html(
        string=_('M.T.R.G (impact)'),
        help=_('Mesures de traitement des risques sur la gravité (relatif à l\'impact)'),
        tracking=True,
    )
    actual_gravity = fields.Selection(
        [
            ('1', _("1")),
            ('2', _("2")),
            ('3', _("3")),
            ('4', _("4")),
        ],
        string=_('Gravité actuelle'),
        tracking=True,
    )
    held = fields.Boolean(
        computed=lambda self: self.actual_gravity and self.actual_gravity > 2,
        string=_('Retenue?'),
        tracking=True,
        store=True,
        readonly=False
    )
    risk_source_ids = fields.Many2many(
        'digiit.ebios_rm.risk.source',
    )

    business_value_id = fields.Many2one(
        'digiit.ebios_rm.business.value',
        ondelete='cascade',
        domain="[('study_id', '=', study_id)]",
        tracking=True,
    )

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_("Etude"),
        ondelete='cascade',
        tracking=True,
    )

    operational_scenario_id = fields.Many2one(
        'digiit.ebios_rm.operational.scenario',
        string=_('Sc. opérationnel'),
        tracking=True,
        ondelete='cascade',
    )

    strategic_scenario_count = fields.Integer(
        string=_('Nombre de scénarios stratégiques'),
        compute='_compute_strategic_scenario_count',
        store=True
    )

    prefix = 'ER-'
    related_field = 'business_value_id'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)

        for rec in records:
            if rec.study_id:
                rec.study_id.message_post(
                    body=f"Événement redouté créé : {rec.name}"
                )

        return records

    def write(self, vals):
        res = super().write(vals)
        # Update strategic scenarios if gravity changed
        if 'actual_gravity' in vals:
            for record in self:
                strategic_scs = self.env['digiit.ebios_rm.visual.strategic.scenario'].search(
                    [('study_id', '=', record.study_id.id), ('dreaded_event_id', '=', record.id)])
                if strategic_scs:
                    strategic_scs.write({'seriousness': vals['actual_gravity']})

        # Post simple message to study
        for rec in self:
            if rec.study_id:
                rec.study_id.message_post(
                    body=f"Événement redouté modifié : {rec.name or rec.title}"
                )

        return res

    def unlink(self):
        # Store info before deletion
        deletion_info = []
        for rec in self:
            if rec.study_id:
                deletion_info.append((rec.study_id, rec.name or rec.title))

        res = super().unlink()

        # Post messages after deletion
        for study, name in deletion_info:
            study.message_post(
                body=f"Événement redouté supprimé : {name}"
            )

        return res

    @api.depends('financial_impact', 'brand_image_impact', 'legal_impact', 'operational_impact')
    def _compute_gross_gravity(self):
        for record in self:
            # Get max value, but ensure it's at least 1 if all fields have values
            impacts = []
            if record.financial_impact:
                impacts.append(int(record.financial_impact))
            if record.brand_image_impact:
                impacts.append(int(record.brand_image_impact))
            if record.legal_impact:
                impacts.append(int(record.legal_impact))
            if record.operational_impact:
                impacts.append(int(record.operational_impact))

            # Only compute if at least one impact is set
            if impacts:
                record.gross_gravity = str(max(impacts))
            else:
                record.gross_gravity = False

    @api.depends('study_id')
    def _compute_strategic_scenario_count(self):
        for record in self:
            record.strategic_scenario_count = self.env['digiit.ebios_rm.visual.strategic.scenario'].search_count([
                ('dreaded_event_id', '=', record.id)
            ])

    @api.onchange('gross_gravity')
    def _update_actual_gravity(self):
        for record in self:
            # Only update if gross_gravity is a valid selection value
            if record.gross_gravity and record.gross_gravity in ['1', '2', '3', '4']:
                record.actual_gravity = record.gross_gravity

    @api.depends('title', 'identifier')
    def _compute_name(self):
        for record in self:
            record.name = f"{record.prefixed_identifier} - {record.title}" if record.title and record.identifier else ""

    @api.depends('business_value_id')
    def _update_prefix(self):
        for record in self:
            record.prefix = f"ER-{record.business_value_id.identifier}-"

    @api.depends("identifier", 'business_value_id')
    def _compute_prefixed_identifier(self):
        for record in self:
            if record.identifier:
                if record.business_value_id:
                    record.prefixed_identifier = f"ER-{record.business_value_id.identifier}-{record.identifier}"
                else:
                    record.prefixed_identifier = f"ER-{record.identifier}"
            else:
                record.prefixed_identifier = ''

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        self._update_prefix()
        return res

    @api.model
    def _get_risk_parameter(self):
        param = self.env['ir.config_parameter'].sudo().get_param('digiit_ebios_rm.accepted_risk_level')
        try:
            return float(param)
        except (ValueError, TypeError):
            return 0

    @api.onchange('actual_gravity')
    def _update_held(self):
        max_value = self._get_risk_parameter()
        self.ensure_one()
        self.held = max_value <= float(self.actual_gravity)
