from odoo import models, fields, api, _


class Ecosystem(models.Model):
    _name = 'digiit.ebios_rm.ecosystem'
    _rec_name = 'display_name'

    display_name = fields.Char(
        string=_('Nom'),
        compute='_compute_display_name',
        store=True
    )

    abbreviation = fields.Char(
        string=_('Abréviation'),
        compute='_compute_display_name',
        store=True,
        help=_('Abréviation de la partie prenante (ex: C1, F2, P3)')
    )

    full_stakeholder_name = fields.Char(
        string=_('Nom complet'),
        compute='_compute_full_stakeholder_name',
        store=True
    )

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_("Etude"),
        ondelete='cascade'
    )
    category = fields.Selection(
        related='stakeholder_id.category',
        string=_('Categorie'),
    )

    stakeholder_id = fields.Many2one(
        'digiit.ebios_rm.stakeholder',
        string=_('Parties prenantes'),
        domain="[('is_internal','=',False)]",
        ondelete="cascade",
        context="{'default_is_internal': False}"
    )

    _sql_constraints = [
        ('unique_stakeholder_per_study',
         'UNIQUE(study_id, stakeholder_id)',
         'This stakeholder already exists in this study!')
    ]

    description = fields.Html(
        string=_('Description'),
        tracking=True,
    )
    dependance = fields.Selection(
        [
            ("1", _("1")),
            ("2", _("2")),
            ("3", _("3")),
            ("4", _("4")),
        ],
        string=_('Dépendence'),
        tracking=True,
        help=_("""
    1 - Relation non nécessaire à la réalisation de la mission.____________________
    2 - Relation utile à la réalisation de la mission.____________________
    3 - Relation indispensable mais non exclusive (Possible substitution).____________________
    4 - Relation indispensable et unique (Pas de substitution possible à court terme).""")
    )
    penetration = fields.Selection(
        [
            ("1", _("1")),
            ("2", _("2")),
            ("3", _("3")),
            ("4", _("4")),
        ],
        string=_('Pénetration'),
        tracking=True,
        help=_("""
    1 - Pas d'accès ou accès avec privilèges de type utilisateurs à des terminaux utilisateurs.____________________
    2 - Accès avec privilèges de type administrateurs à des terminaux utilisateurs ou accès physique.____________________
    3 - Accès avec privilèges de type administrateurs à des serveurs métier.____________________
    4 - Accès avec privilèges de type administrateurs à des équipements d'infrastructure.""")
    )
    exposition = fields.Integer(
        string=_('Exposition'),
        tracking=True,
        compute='_compute_exposition',
        store=True,
    )
    maturity = fields.Selection(
        [
            ("1", _("1")),
            ("2", _("2")),
            ("3", _("3")),
            ("4", _("4")),
        ],
        string=_('Maturité'),
        tracking=True,
        help=_("""
    1 - Des règles d'hygiène informatique sont appliquées ponctuellement et non formalisées.____________________
    2 - Les règles d'hygiène et la réglementation sont prises en compte, sans intégration globale.____________________
    3 - Une politique globale est appliquée en matière de sécurité numérique.____________________
    4 - Mise en œuvre d'une politique de management du risque.""")
    )
    confidance = fields.Selection(
        [
            ("1", _("1")),
            ("2", _("2")),
            ("3", _("3")),
            ("4", _("4")),
        ],
        string=_('Confiance'),
        tracking=True,
        help=_("""
    1 - Les intentions de la partie prenante ne sont pas connues.____________________
    2 - Les intentions de la partie prenante sont considérées comme neutres.____________________
    3 - Les intentions de la partie prenante sont connues et probablement positives.____________________
    4 - Les intentions de la partie prenante sont parfaitement connues et compatibles.""")
    )

    cyber_reliability = fields.Integer(
        string=_('Fiablilité Cyber'),
        compute='_compute_cyber_reliability',
        tracking=True,
        store=True,
    )

    threat_level = fields.Float(
        string=_('Niveau de menace'),
        tracking=True,
    )

    security_measures = fields.Html(
        string=_('Mesures de sécurité pour réduire le niveau de menace'),
        help=_('Mesures de sécurité pour réduire le niveau de menace'),
        tracking=True,
    )

    equivalent_mesures_referentiel_to_implement = fields.Many2many(
        'digiit.ebios_rm.study.measure',
        string=_('Mesures à implementer provenant des référentiels de l\'étude'),
        domain="[('study_id', '=', study_id)]",
        tracking=True,
    )

    residual_threat_level = fields.Float(
        string=_('Niveau de menace résiduel'),
        tracking=True,
        required=True,
    )

    saved_value = fields.Float(default=0)
    residual_threat_level_computed = fields.Boolean(default=False)

    color_index = fields.Selection(
        [
            ('red', 'Rouge'),
            ('yellow', 'Jaune'),
            ('blue', 'Bleue'),
            ('green', 'Vert'),
        ],
        string="Index de couleur",
        help="Color indicator based on cyber reliability",
        compute="_compute_color_index",
        store=True,
    )

    zone_index = fields.Selection(
        [
            ('danger', 'Zone de danger'),
            ('control', 'Zone de contrôle'),
            ('veille', 'Zone de surveillance'),
        ],
        string="Indice de zone",
        help="Zone de risque basé sur le niveau de risque",
        compute="_compute_zone_index",
        store=True,
    )

    stakeholder_size = fields.Selection(
        [
            ('xs', 'Extra Small'),
            ('sm', 'Faible'),
            ('md', 'Medium'),
            ('lg', 'Large'),
        ],
        string="Taille des parties prenantes",
        help="Taille visuelle basée sur le niveau d\'exposition",
        compute="_compute_stakeholder_size",
        store=True,
    )

    # critique = fields.Boolean(
    #     string=_('Critique'),
    #     compute='_compute_critique',
    #     inverse='_inverse_critique',
    #     store=True,
    #     tracking=True,
    #     help=_('Si coché, cette partie prenante sera affichée dans la cartographie des menaces. '
    #            'Automatiquement défini en fonction du niveau de menace critique.')
    # )
    #
    # critique_manual = fields.Boolean(
    #     default=False,
    #     help="Flag indicating if critique was manually set by user"
    # )
    #
    # critique_threshold_crossed = fields.Boolean(
    #     compute='_compute_critique_threshold_crossed',
    #     store=False,
    #     help="Indicates if threat_level crossed the critique threshold"
    # )

    justification = fields.Text(
        string=_('Justification'),
        tracking=True
    )

    @api.depends('stakeholder_id', 'stakeholder_id.name', 'category')
    def _compute_display_name(self):
        # Define category prefixes
        category_prefixes = {
            'customers': 'C',
            'personnels': 'M',
            'prestataires': 'F',
            'partenaires': 'P'
        }

        for record in self:
            if record.stakeholder_id and record.stakeholder_id.name:
                base_name = record.stakeholder_id.name

                # Add prefix based on category if available
                if record.category:
                    prefix = category_prefixes.get(record.category, 'P')

                    # Count existing ecosystems with same category in this study
                    # Only count if record has a real ID (not NewId)
                    if isinstance(record.id, int):
                        count = self.env['digiit.ebios_rm.ecosystem'].search_count([
                            ('study_id', '=', record.study_id.id),
                            ('category', '=', record.category),
                            ('id', '<=', record.id)
                        ])
                    else:
                        # For new records (NewId), count all existing + 1
                        count = self.env['digiit.ebios_rm.ecosystem'].search_count([
                            ('study_id', '=', record.study_id.id),
                            ('category', '=', record.category),
                        ]) + 1

                    record.abbreviation = f"{prefix}{count}"
                    record.display_name = f"{record.abbreviation} - {base_name}"
                else:
                    record.abbreviation = ''
                    record.display_name = base_name

            elif record.stakeholder_id:
                record.abbreviation = ''
                record.display_name = record.stakeholder_id.name if hasattr(record.stakeholder_id,
                                                                            'name') else f'Ecosystem {record.id}'
            else:
                record.abbreviation = ''
                record.display_name = f'Ecosystem {record.id}'

    @api.depends('stakeholder_id', 'stakeholder_id.name')
    def _compute_full_stakeholder_name(self):
        for record in self:
            if record.stakeholder_id and record.stakeholder_id.name:
                record.full_stakeholder_name = record.stakeholder_id.name
            elif record.stakeholder_id:
                record.full_stakeholder_name = record.stakeholder_id.name if hasattr(record.stakeholder_id,
                                                                                     'name') else ''
            else:
                record.full_stakeholder_name = ''

    def name_get(self):
        """Override to show full name in specific contexts"""
        result = []
        for record in self:
            if self.env.context.get('show_full_name') and record.full_stakeholder_name:
                # Show: "C1 - Full Company Name"
                if record.abbreviation:
                    name = f"{record.abbreviation} - {record.full_stakeholder_name}"
                else:
                    name = record.full_stakeholder_name
            else:
                name = record.display_name
            result.append((record.id, name))
        return result

    # @api.depends('threat_level')
    # def _compute_critique_threshold_crossed(self):
    #     """Check if threat level crosses the critique threshold"""
    #     critique_limit = self.env['digiit.ebios_rm.threat.level.limit'].search(
    #         [('name', '=', 'Critique')], limit=1
    #     )
    #
    #     for record in self:
    #         if critique_limit:
    #             # Check if we're crossing the threshold
    #             is_above = record.threat_level >= critique_limit.value
    #             record.critique_threshold_crossed = is_above
    #         else:
    #             record.critique_threshold_crossed = True
    #
    # @api.depends('threat_level', 'critique_manual', 'critique_threshold_crossed')
    # def _compute_critique(self):
    #     """
    #     Compute critique based on threat_level.
    #     Respects manual override UNLESS threat_level changes significantly.
    #     """
    #     critique_limit = self.env['digiit.ebios_rm.threat.level.limit'].search(
    #         [('name', '=', 'Critique')], limit=1
    #     )
    #
    #     for record in self:
    #         # Calculate what the automatic value should be
    #         auto_critique = (
    #             record.threat_level >= critique_limit.value if critique_limit else True
    #         )
    #
    #         if record.critique_manual:
    #             # User has manually set critique
    #             # Only override if threat_level crosses threshold in opposite direction
    #             if record.critique and not auto_critique:
    #                 # Was manually set to True, but threat dropped below threshold
    #                 # Reset to automatic
    #                 record.critique_manual = False
    #                 record.critique = auto_critique
    #             elif not record.critique and auto_critique:
    #                 # Was manually set to False, but threat rose above threshold
    #                 # Reset to automatic
    #                 record.critique_manual = False
    #                 record.critique = auto_critique
    #             # else: keep manual value (no threshold crossing)
    #         else:
    #             # Use automatic calculation
    #             record.critique = auto_critique
    #
    # def _inverse_critique(self):
    #     """When user manually changes critique, set the manual flag"""
    #     for record in self:
    #         critique_limit = self.env['digiit.ebios_rm.threat.level.limit'].search(
    #             [('name', '=', 'Critique')], limit=1
    #         )
    #         auto_critique = (
    #             record.threat_level >= critique_limit.value if critique_limit else True
    #         )
    #
    #         # Only mark as manual if user's choice differs from automatic
    #         if record.critique != auto_critique:
    #             record.critique_manual = True
    #         else:
    #             record.critique_manual = False

    @api.model_create_multi
    def create(self, vals):
        recs = super(Ecosystem, self).create(vals)

        for rec in recs:
            rec.study_id.message_post(
                body=(
                    f"Partie intéressée créée : {rec.stakeholder_id.name}"
                )
            )

        return recs

    def write(self, vals):
        # Capture old values before write
        old_values = {}
        if 'threat_level' in vals:
            for rec in self:
                old_values[rec.id] = rec.threat_level

        # Perform write once
        res = super().write(vals)

        # Post messages after write
        for rec in self:
            if rec.study_id:
                changes = []
                if 'threat_level' in vals and rec.id in old_values:
                    old_threat = old_values[rec.id]
                    changes.append(f"Niveau de menace : {old_threat:.2f} → {rec.threat_level:.2f}")

                if changes:
                    rec.study_id.message_post(
                        body=f"Partie intéressée mise à jour : {rec.stakeholder_id.name}\n" + "\n".join(
                            changes)
                    )

        return res

    def unlink(self):
        for rec in self:
            rec.study_id.message_post(
                body=(
                    f"Partie intéressée supprimée : {rec.stakeholder_id.name}"
                )
            )

        return super(Ecosystem, self).unlink()

    @api.depends('cyber_reliability')
    def _compute_color_index(self):
        for record in self:
            if record.cyber_reliability < 4:
                record.color_index = 'red'
            elif record.cyber_reliability < 6:
                record.color_index = 'yellow'
            elif record.cyber_reliability < 8:
                record.color_index = 'blue'
            else:
                record.color_index = 'green'

    @api.depends('threat_level')
    def _compute_zone_index(self):
        for record in self:
            if record.threat_level >= 5:
                record.zone_index = 'danger'
            elif record.threat_level >= 2:
                record.zone_index = 'control'
            else:
                record.zone_index = 'veille'

    @api.depends('exposition')
    def _compute_stakeholder_size(self):
        for record in self:
            if record.exposition < 3:
                record.stakeholder_size = 'xs'
            elif record.exposition < 7:
                record.stakeholder_size = 'sm'
            elif record.exposition < 10:
                record.stakeholder_size = 'md'
            else:
                record.stakeholder_size = 'lg'

    @api.depends('dependance', 'penetration')
    def _compute_exposition(self):
        for record in self:
            if record.dependance and record.penetration:
                record.exposition = int(record.dependance) * int(record.penetration)
            else:
                record.exposition = 0

    @api.depends('maturity', 'confidance')
    def _compute_cyber_reliability(self):
        for record in self:
            if record.maturity and record.confidance:
                record.cyber_reliability = int(record.maturity) * int(record.confidance)
            else:
                record.cyber_reliability = 0

    @api.onchange('exposition', 'cyber_reliability')
    def _compute_threat_level(self):
        print("OK i'm calculating\n\n\n")
        for record in self:
            if record.dependance and record.penetration and record.maturity and record.confidance:
                record.threat_level = int(record.exposition) / int(record.cyber_reliability)
            else:
                record.threat_level = 0

    @api.onchange('threat_level')
    def _treat_changing_threat_level(self):
        for record in self:
            record.residual_threat_level = record.threat_level

    @api.onchange('residual_threat_level')
    def _treat_changing_residual_threat_level(self):
        for record in self:
            print("record.saved_value = ", record.saved_value)
            print("record.residual_threat_level = ", record.residual_threat_level)
            record.saved_value = record.residual_threat_level

    def is_retenu(self):
        self.ensure_one()
        return self._is_retenue_value(self.threat_level)

    def _is_retenue_value(self, val):
        limits = self.env['digiit.ebios_rm.threat.level.limit'].search([('held', '=', True)], order='value asc', limit=1)
        if (len(limits) == 0):
            return False

        if (val <= limits[0].value):
            return False
        return True

    def is_unique(self):
        return self.env['digiit.ebios_rm.ecosystem'].search_count(
            [('study_id', '=', self.study_id.id), ('id', '!=', self.id),
             ('stakeholder_id', '=', self.stakeholder_id.id)]) == 0

    def build_cartography_elements(self):
        """
        Prepare ecosystem elements for threat mapping visualization.
        This method calculates and updates necessary fields for visualization.
        """
        for record in self:
            # Ensure all required fields are calculated
            if not record.exposition:
                record._compute_exposition()

            if not record.cyber_reliability:
                record._compute_cyber_reliability()

            # Calculate threat level if not already set
            if not record.threat_level:
                if record.cyber_reliability > 0:  # Avoid division by zero
                    record.threat_level = record.exposition / record.cyber_reliability
                else:
                    record.threat_level = record.exposition  # Default to exposition if cyber_reliability is 0

            # Color mapping based on cyber_reliability
            if record.cyber_reliability < 4:
                record.color_index = 'red'
            elif record.cyber_reliability < 6:
                record.color_index = 'yellow'
            elif record.cyber_reliability < 8:
                record.color_index = 'blue'
            else:
                record.color_index = 'green'

            # Zone mapping based on threat_level
            if record.threat_level >= 5:
                record.zone_index = 'danger'
            elif record.threat_level >= 2:
                record.zone_index = 'control'
            else:
                record.zone_index = 'veille'

            # Size mapping based on exposition
            if record.exposition < 3:
                record.stakeholder_size = 'xs'
            elif record.exposition < 7:
                record.stakeholder_size = 'sm'
            elif record.exposition < 10:
                record.stakeholder_size = 'md'
            else:
                record.stakeholder_size = 'lg'
