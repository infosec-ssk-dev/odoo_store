from odoo import models, fields, _, api
from odoo.osv import expression
import json

class Study(models.Model):
    _name = 'digiit.ebios_rm.study'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Etude'

    name = fields.Char(string=_('Nom de l\'application'), tracking=True)
    study_context = fields.Text(string=_('Contexte'), tracking=True)
    is_for_partner = fields.Boolean(
        string=_('Etude pour un partenaire?'),
        tracking=True,
        required=True,
        default=False
    )
    select_company = fields.Boolean(compute='_compute_select_company')
    def _compute_select_company(self):
        for record in self:
            record.select_company = len(self.env.user.company_ids) > 1

    selected_companies = fields.Many2many(
        'res.company',
        string=_('Sociétés'),
        compute='_compute_selected_companies',
    )

    def _compute_selected_companies(self):
        for record in self:
            record.selected_companies = self.env.user.company_ids

    current_company = company_id = fields.Many2one(
        'res.company',
        string=_('Entreprise'),
        compute=lambda self: self.env.company.id,
    )
    company_id = fields.Many2one(
        'res.company',
        string=_('Entreprise'),
        required=True,
        default=lambda self: self.env.company.id,
        tracking=True,
    )

    owner_company = fields.Char(
        string=_('Propriétaire'),
        compute='_compute_owner'
    )

    def _compute_owner(self):
        for record in self:
            record.owner_company = record.partner.name if record.is_for_partner else record.company_id.name

    partner = fields.Many2one(
        'res.partner',
        string=_('Partenaire'),
        domain="[('is_company', '=', True)]",
        tracking=True,
    )

    study_date = fields.Date(string=_('Date'), default=fields.Date.today, tracking=True)
    objects = fields.One2many(
        'digiit.ebios_rm.study.object',
        'study_id',
        string=_("Study Object")
    )

    state = fields.Selection(
        [
            ('workshop1', _("Atelier 1")),
            ('workshop2', _("Atelier 2")),
            ('workshop3', _("Atelier 3")),
            ('workshop4', _("Atelier 4")),
            ('workshop5', _("Atelier 5")),
        ],
        string=_("Etape"),
        default='workshop1',
        required=True,
    )

    perimeter = fields.Selection(
        [
            ('smsi', _("SMSI")),
        ],
        string=_("Périmètre"),
        default='smsi',
        required=True, tracking=True
    )

    strategic_cycle = fields.Integer(
        string=_('Cycle stratégique'),
        required=True,
        default=3, tracking=True
    )

    operational_cycle = fields.Integer(
        string=_('Cycle opérationnel'),
        required=True,
        default=1, tracking=True
    )

    iterations = fields.One2many(
        'digiit.ebios_rm.iteration',
        'study_id',
        string=_('Iterations'), required=True
    )

    mission = fields.Text(string=_('Mission'), tracking=True)

    participants = fields.Html(
        string=_('Participants')
    )

    constraints_hypotheses = fields.Text(
        string=_('Contraintes et hypothèses'),
        tracking=True
    )

    project_planning_elements = fields.Html(
        string=_("Planning des ateliers de l'étude")
    )

    risk_acceptation_criteria = fields.Selection(
        [
            ('minor', _("Minor")),
            ('avarage', _("Moyenne")),
            ('strong', _("Élevée")),
            ('critical', _("Critique")),
        ],
        string=_("Critères d\'acceptation des risques"),
        default="minor",
        required=True, tracking=True
    )

    business_value_ids = fields.One2many(
        'digiit.ebios_rm.business.value',
        'study_id',
        ondelete='cascade',
        string=_('Valeurs métiers'), required=True
    )

    risk_source_ids = fields.One2many(
        'digiit.ebios_rm.risk.source',
        'study_id',
        string=_('Source de risque'),
        required=True,
    )
    ecosystem_ids = fields.One2many(
        'digiit.ebios_rm.ecosystem',
        'study_id',
        string=_('Ecosystème'),
        required=True, 
    )

    risk_treatment_ids = fields.One2many(
        'digiit.ebios_rm.risk.treatment',
        'study_id',
        string=_('Risk treatment'),
    )

    is_applicable = fields.Boolean(
        string="Mettre tous les autres mesures comme applicables comme bonne pratique",
        default= False,
        tracking= True,
    )

    operational_scenario_ids = fields.One2many(
        'digiit.ebios_rm.operational.scenario',
        'study_id',
        string=_('Sc. opérationnels')
    )

    visual_strategic_scenario_ids = fields.One2many(
        'digiit.ebios_rm.visual.strategic.scenario',
        'study_id',
        string=_('Scénarios stratégiques visuels')
    )

    dreaded_event_ids = fields.One2many(
        'digiit.ebios_rm.dreaded.event',
        'study_id',
        string=_('Evénements redoutés'),
    )
    selected_stakeholder_ids = fields.Many2many(
        'digiit.ebios_rm.stakeholder',
        string='Parties prenantes sélectionnées',
        compute='_compute_stakeholders',
        store=False,
    )

    elite_stakeholder_ids = fields.Many2many(
        'digiit.ebios_rm.stakeholder',
        string='Parties prenantes sélectionnées',
        domain="[('id','in',business_value_ids.mapped('stakeholder_ids').ids)]",
        store=False,
    )

    strategic_scenarios_count = fields.Integer(
        string=_(''),
        compute='_get_strategic_scenarios_count'
    )

    operational_scenarios_count = fields.Integer(
        string=_(''),
        compute='_get_operational_scenarios_count'
    )

    risk_treatments_count = fields.Integer(
        string=_(''),
        compute='_get_risk_treatments_count'
    )

    ecosystems_count = fields.Integer(
        string=_(''),
        compute='_get_ecosystems_count'
    )

    risk_sources_count = fields.Integer(
        string=_(''),
        compute='_get_risk_sources_count'
    )

    business_values_count = fields.Integer(
        string=_(''),
        compute='_get_business_values_count'
    )

    dreaded_events_count = fields.Integer(
        string=_(''),
        compute='_get_dreaded_events_count'
    )

    held_dreaded_events_count = fields.Integer(
        string=_('Evénements redoutés retenus'),
        compute='_get_held_dreaded_events_count'
    )

    held_risk_sources_count = fields.Integer(
        string=_('Sources de risque retenues'),
        compute='_get_held_risk_sources_count'
    )

    visual_strategic_sc_enabled = fields.Boolean(
        string=_('Graphique Sc. stratégique visible'),
        default=False
    )
    study_referential_ids = fields.One2many(
        'digiit.ebios_rm.study.referential',
        'study_id',
        string=_('Référentiels de l\'étude'),
        tracking=True
    )

    referentials_count = fields.Integer(
        string=_('Nombre de référentiels'),
        compute='_get_referentials_count'
    )
    study_chapter_ids = fields.One2many('digiit.ebios_rm.study.chapter', 'study_id', string='Chapitres')
    study_measure_ids = fields.One2many('digiit.ebios_rm.study.measure', 'study_id', string='Mesures')
    study_question_ids = fields.One2many('digiit.ebios_rm.study.question', 'study_id', string='Questions')

    study_pacs_ids = fields.One2many(
        'digiit.ebios_rm.study.pacs',
        'study_id',
        string=_('PACS')
    )

    pacs_count = fields.Integer(
        string=_('Nombre de PACS'),
        compute='_get_pacs_count'
    )

    #captures fields for ROTO cartos
    roto_risk_origin_chart_image = fields.Binary("Legend for RO cartography image")
    roto_target_objective_chart_image = fields.Binary("Legend for TO cartography image")
    roto_legend_image = fields.Binary("Legend for ROTO cartography image")

    #captures fields for PIP cartos
    actual_cartography_image = fields.Binary("Actual PIP Cartography Image")
    residual_cartography_image = fields.Binary("Residual PIP Cartography Image")
    cartography_legend_image = fields.Binary("Legend for PIP cartography image")

    #captures fields for strategic scenarios visuals
    strategic_scenario_images = fields.Text(
        string='Strategic Scenario Images',
        help='JSON data containing captured strategic scenario diagrams'
    )

    #capture field for risk matrix
    risk_matrix_image = fields.Binary("Risk Matrix Image")

    @api.depends('study_pacs_ids')
    def _get_pacs_count(self):
        for record in self:
            record.pacs_count = len(record.study_pacs_ids)

    def action_view_pacs(self):
        """Open PACS for this study"""
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("digiit_ebios_rm.action_study_pacs_list_view")
        action['domain'] = [('study_id', '=', self.id)]
        action['context'] = {'default_study_id': self.id}
        action['search_view_id'] = False
        return action

    def populate_pacs(self):
        for study in self:
            unique_measures = set()

            # Collect unique measures from risk treatments
            risk_treatments = self.env['digiit.ebios_rm.risk.treatment'].search([
                ('study_id', '=', study.id),
                ('equivalent_mesures_referentiel', '!=', False)
            ])

            for rt in risk_treatments:
                for measure in rt.equivalent_mesures_referentiel:
                    unique_measures.add(measure.id)

            # Collect unique measures from ecosystems
            ecosystems = self.env['digiit.ebios_rm.ecosystem'].search([
                ('study_id', '=', study.id),
                ('equivalent_mesures_referentiel_to_implement', '!=', False)
            ])

            for ecosystem in ecosystems:
                for measure in ecosystem.equivalent_mesures_referentiel_to_implement:
                    unique_measures.add(measure.id)

            # Collect measures from questions with response_application = 'oui' (security baseline)
            answered_no_questions = self.env['digiit.ebios_rm.study.question'].search([
                ('study_id', '=', study.id),
                ('response_application', '=', 'non')
            ])

            for question in answered_no_questions:
                if question.study_measure_id:
                    unique_measures.add(question.study_measure_id.id)

            # Get existing PACS
            existing_pacs = self.env['digiit.ebios_rm.study.pacs'].search([
                ('study_id', '=', study.id)
            ])
            existing_measure_ids = set(existing_pacs.mapped('study_measure_id').ids)

            # **NEW: Remove PACS that are no longer referenced**
            orphaned_pacs = existing_pacs.filtered(
                lambda p: p.study_measure_id.id not in unique_measures
            )
            if orphaned_pacs:
                orphaned_pacs.unlink()

            # Create PACS for new measures
            pacs_vals = []
            for measure_id in unique_measures:
                if measure_id not in existing_measure_ids:
                    pacs_vals.append({
                        'study_id': study.id,
                        'study_measure_id': measure_id,
                    })

            if pacs_vals:
                self.env['digiit.ebios_rm.study.pacs'].create(pacs_vals)

            # Force recompute of computed fields for remaining PACS
            remaining_pacs = self.env['digiit.ebios_rm.study.pacs'].search([
                ('study_id', '=', study.id)
            ])
            if remaining_pacs:
                remaining_pacs._compute_risk_treatments()
                remaining_pacs._compute_ecosystems()
                remaining_pacs._compute_risk_treatment_count()
                remaining_pacs._compute_security_baseline_applicable()

    @api.depends('study_referential_ids')
    def _get_referentials_count(self):
        for record in self:
            record.referentials_count = len(record.study_referential_ids)

    def action_view_referentials(self):
        """Open questions for this study"""
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("digiit_ebios_rm.action_study_question_list_view")
        action['domain'] = [('study_id', '=', self.id)]
        action['context'] = {'default_study_id': self.id}
        action['search_view_id'] = False
        return action

    def _get_business_values_count(self):
        for record in self:
            record.business_values_count = len(self.business_value_ids)

    def _get_dreaded_events_count(self):
        for record in self:
            record.dreaded_events_count = len(self.dreaded_event_ids)

    def _get_strategic_scenarios_count(self):
        for record in self:
            record.strategic_scenarios_count = len(self.visual_strategic_scenario_ids)

    def _get_operational_scenarios_count(self):
        for record in self:
            record.operational_scenarios_count = len(self.operational_scenario_ids)

    def _get_risk_treatments_count(self):
        for record in self:
            record.risk_treatments_count = len(self.risk_treatment_ids)

    def _get_ecosystems_count(self):
        for record in self:
            record.ecosystems_count = len(self.ecosystem_ids)

    def _get_risk_sources_count(self):
        for record in self:
            record.risk_sources_count = len(self.risk_source_ids)

    @api.depends('dreaded_event_ids.held')
    def _get_held_dreaded_events_count(self):
        for record in self:
            record.held_dreaded_events_count = len(record.dreaded_event_ids.filtered(lambda e: e.held))

    # NEW: Compute held risk sources count
    @api.depends('risk_source_ids.held')
    def _get_held_risk_sources_count(self):
        for record in self:
            record.held_risk_sources_count = len(record.risk_source_ids.filtered(lambda r: r.held))


    def action_view_business_values(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("digiit_ebios_rm.action_business_value_list_view")
        action['domain'] = [('study_id', '=', self.id)]
        action['context'] = {'default_study_id': self.id}
        action['search_view_id'] = False
        return action

    def action_view_dreaded_events(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("digiit_ebios_rm.action_dreaded_event_list_view")
        action['domain'] = [('study_id', '=', self.id)]
        action['context'] = {'default_study_id': self.id}
        action['search_view_id'] = False
        return action

    def action_view_ecosystems(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("digiit_ebios_rm.action_ecosystem_list_view")
        action['domain'] = [('study_id', '=', self.id)]
        action['context'] = {'default_study_id': self.id}
        action['search_view_id'] = False
        return action

    def action_view_risk_sources(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("digiit_ebios_rm.action_risk_source_list_view")
        action['domain'] = [('study_id', '=', self.id)]
        action['context'] = {'default_study_id': self.id}
        action['search_view_id'] = False
        return action

    def display_visual(self):
        for record in self:
            # Clear existing risk scenarios
            records_to_delete = self.env['digiit.ebios_rm.risk.scenario'].search([('study_id', '=', record.id)])
            if records_to_delete:
                records_to_delete.unlink()

            # Ensure visual strategic scenarios are up-to-date
            if record.visual_strategic_scenario_ids:
                for visual_scenario in record.visual_strategic_scenario_ids:
                    visual_scenario.build_grafical_elements()

        return {
            'type': 'ir.actions.client',
            'tag': 'risk_dashboard_ebios_rm.dashboard',
            'target': 'current',
            'context': {
                'study_id': self.id
            }
        }

    def action_open_roto_radar_cartography(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'digiit_ebios_rm.roto_radar_cartography',
            'target': 'current',
            'name': _('Cartographies SR/OV – %s') % self.name,
            'params': {
                'study_id': self.id,
            },
        }

    def _auto_save(self):
        print("I'm saving studies")
        self.ensure_one()
        self.env['digiit.ebios_rm.study'].browse(self.id).write({
            'name': self.name,
            'study_context': self.study_context,
            'company_id': self.company_id,
            'study_date': self.study_date,
            'objects': self.objects,
            'state': self.state,
            'perimeter': self.perimeter,
            'strategic_cycle': self.strategic_cycle,
            'operational_cycle': self.operational_cycle,
            'iterations': self.iterations,
            'mission': self.mission,
            'risk_acceptation_criteria': self.risk_acceptation_criteria,
            'business_value_ids': self.business_value_ids,
            'risk_source_ids': self.risk_source_ids,
            'ecosystem_ids': self.ecosystem_ids,
            'risk_treatment_ids': self.risk_treatment_ids,
            'operational_scenario_ids': self.operational_scenario_ids,
            'dreaded_event_ids': self.dreaded_event_ids,
        })

    @api.model
    def _search(self, args, offset=0, limit=None, order=None):
        args = expression.AND([args, [('company_id', '=', self.env.company.id)]])
        return super(Study, self)._search(args, offset=offset, limit=limit, order=order)

    @api.depends('business_value_ids.stakeholder_ids')
    def _compute_stakeholders(self):
        for study in self:
            selected_ids = study.business_value_ids.mapped('stakeholder_ids').ids
            internal_ids = self.env['digiit.ebios_rm.stakeholder'].search([('is_internal', '=', True)]).ids
            study.selected_stakeholder_ids = list(set(selected_ids + internal_ids))


    def display_visual_pip(self):
        for record in self:
            ecosystems = self.env['digiit.ebios_rm.ecosystem'].search([('study_id', '=', record.id)])
            for ecosystem in ecosystems:
                ecosystem.build_cartography_elements()

        return {
            'type': 'ir.actions.client',
            'tag': 'digiit_ebios_rm.threat_map',
            'target': 'current',
            'context': {
                'study_id': self.id
            }
        }

    def display_visual_risk_matrix(self):
        return {
            'type': 'ir.actions.client',
            'tag': 'digiit_ebios_rm.risk_matrix',
            'target': 'current',
            'context': {
                'study_id': self.id
            }
        }


    def populate_pip(self):
        for record in self:
            pip_ids = record.business_value_ids.mapped('stakeholder_ids').ids
            unique_pips = self.env['digiit.ebios_rm.stakeholder'].browse(list(set(pip_ids)))

            ecosystem_vals = []
            for pip in unique_pips:
                if not self.env['digiit.ebios_rm.ecosystem'].search_count([
                    ('study_id', '=', record.id),
                    ('stakeholder_id', '=', pip.id)
                ]):
                    ecosystem_vals.append({
                        'study_id': record.id,
                        'stakeholder_id': pip.id,
                        'category': 'personnels',
                        'residual_threat_level': 0.0,
                    })

            if ecosystem_vals:
                self.env['digiit.ebios_rm.ecosystem'].create(ecosystem_vals)


    def populate(self):
        for record in self:
            self._create_visual_strategic_sc(record.ecosystem_ids, record.id)
            record.visual_strategic_sc_enabled = True

    def _create_visual_strategic_sc(self, ecosystems, study_id):
        visual_sc_dictionary = {}
        risk_sources = self.env['digiit.ebios_rm.risk.source'].search([('study_id', '=', study_id), ('held', '=', True)])
        print("risk_sources: ",risk_sources)
        for risk_source in risk_sources:
            print("len(risk_source.dreaded_event_ids): ",len(risk_source.dreaded_event_ids))
            if len(risk_source.dreaded_event_ids) == 0:
                continue
            for dreaded_event in risk_source.dreaded_event_ids:
                if self.env['digiit.ebios_rm.visual.strategic.scenario'].search_count(
                        [('study_id', '=', study_id), ('risk_source_id', '=', risk_source.id),
                         ('dreaded_event_id', '=', dreaded_event.id)]) == 0:
                    if f"srov{risk_source.id}-er{dreaded_event.id}" not in visual_sc_dictionary:
                        visual_sc_dictionary[f"srov{risk_source.id}-er{dreaded_event.id}"] = {
                            'study_id': study_id,
                            'risk_source_id': risk_source.id,
                            'stakeholder_ids': [],
                            'dreaded_event_id': dreaded_event.id,
                            'seriousness': dreaded_event.actual_gravity,
                        }

        for record in ecosystems:
            if record.is_retenu():
                for risk_source in risk_sources:
                    if len(risk_source.dreaded_event_ids) == 0:
                        continue
                    selected_dreaded_events = risk_source.dreaded_event_ids.search(
                        [('study_id', '=', record.study_id.id),
                         ('business_value_id.stakeholder_ids', 'in', [record.stakeholder_id.id]),
                         ('id', 'in', risk_source.dreaded_event_ids.ids)])

                    if record.stakeholder_id.is_internal or len(selected_dreaded_events) == 0:
                        pass
                    else:
                        for dreaded_event in selected_dreaded_events:
                            if self.env['digiit.ebios_rm.visual.strategic.scenario'].search_count(
                                    [('study_id', '=', record.study_id.id), ('risk_source_id', '=', risk_source.id),
                                     ('dreaded_event_id', '=', dreaded_event.id),
                                     ('stakeholder_ids', 'in', [record.stakeholder_id.id])]) == 0:
                                if f"srov{risk_source.id}-er{dreaded_event.id}" not in visual_sc_dictionary:
                                    visual_sc_dictionary[f"srov{risk_source.id}-er{dreaded_event.id}"] = {
                                        'study_id': record.study_id.id,
                                        'risk_source_id': risk_source.id,
                                        'stakeholder_ids': [],
                                        'dreaded_event_id': dreaded_event.id,
                                        'seriousness': dreaded_event.actual_gravity,
                                    }
                                visual_sc_dictionary[f"srov{risk_source.id}-er{dreaded_event.id}"][
                                    'stakeholder_ids'].append(record.stakeholder_id.id)
        if len(visual_sc_dictionary) > 0:
            for scenario in visual_sc_dictionary:
                built_sc = self.env['digiit.ebios_rm.visual.strategic.scenario'].create(visual_sc_dictionary[scenario])
                built_sc.build_grafical_elements()

    def populate_operational_sc(self):
        for record in self:
            if len(record.visual_strategic_scenario_ids) > 0:
                for sc_sc in record.visual_strategic_scenario_ids:
                    self._create_operational_scenario_from_strategic_sc(sc_sc)

    def _create_operational_scenario_from_strategic_sc(self, record):
        if record._is_held():
            # Create direct attack scenario (no stakeholder)
            if self.env['digiit.ebios_rm.operational.scenario'].search_count([
                ('study_id', '=', record.study_id.id),
                ('risk_source_id', '=', record.risk_source_id.id),
                ('dreaded_event_id', '=', record.dreaded_event_id.id),
                ('stakeholder_id', '=', False)
            ]) == 0:
                self.env['digiit.ebios_rm.operational.scenario'].create({
                    'study_id': record.study_id.id,
                    'risk_source_id': record.risk_source_id.id,
                    'dreaded_event_id': record.dreaded_event_id.id,
                    'seriousness': record.dreaded_event_id.actual_gravity if record.dreaded_event_id else '0',
                    'direct_attack': True,
                    'strategic_sc_id': record.id
                })

            # Create indirect attack scenarios (with stakeholders)
            for stakeholder in record.stakeholder_ids:
                if self.env['digiit.ebios_rm.operational.scenario'].search_count([
                    ('study_id', '=', record.study_id.id),
                    ('risk_source_id', '=', record.risk_source_id.id),
                    ('dreaded_event_id', '=', record.dreaded_event_id.id),
                    ('stakeholder_id', '=', stakeholder.id)
                ]) == 0:
                    self.env['digiit.ebios_rm.operational.scenario'].create({
                        'study_id': record.study_id.id,
                        'risk_source_id': record.risk_source_id.id,
                        'dreaded_event_id': record.dreaded_event_id.id,
                        'seriousness': record.dreaded_event_id.actual_gravity if record.dreaded_event_id else '0',
                        'direct_attack': False,
                        'stakeholder_id': stakeholder.id,
                        'strategic_sc_id': record.id
                    })

    def populate_risk_treatments(self):
        for record in self:
            if len(record.operational_scenario_ids) > 0:
                for op_sc in record.operational_scenario_ids:
                    self._create_risk_treatment(op_sc)

    @api.model
    def prepare_dashboard_data(self, study_id):
        """
        Prepares all necessary data for the strategic scenarios visual by ensuring graphical elements
        are built and up-to-date.
        """
        study = self.browse(study_id)
        if not study.exists():
            return {'error': 'Study not found'}

        # Find all strategic scenarios for this study and rebuild their graphical elements.
        # with the latest data from the ecosystem.
        strategic_scenarios = self.env['digiit.ebios_rm.visual.strategic.scenario'].search([
            ('study_id', '=', study_id)
        ])

        for sc in strategic_scenarios:
            sc.build_grafical_elements()

        return {'status': 'success'}

    def _create_risk_treatment(self, record):
        if self.env['digiit.ebios_rm.risk.treatment'].search_count(
                [('study_id', '=', record.study_id.id), ('risk_id', '=', record.id)]) == 0:
            self.env['digiit.ebios_rm.risk.treatment'].create({
                'study_id': record.study_id.id,
                'risk_id': record.id,
            })

    @api.model
    def default_get(self, fields_list):
        res = super(Study, self).default_get(fields_list)
        return res

    def action_view_sc_strategique(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("digiit_ebios_rm.action_visual_strategic_scenario_list_view")
        action['domain'] = [('study_id', '=', self.id)]
        action['context'] = {'default_study_id': self.id}
        action['search_view_id'] = False
        return action

    def action_operational_scenario_list_view(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("digiit_ebios_rm.action_operational_scenario_list_view")
        action['domain'] = [('study_id', '=', self.id)]
        action['context'] = {'default_study_id': self.id}
        action['search_view_id'] = False
        return action

    def action_risk_treatment_list_view(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("digiit_ebios_rm.action_risk_treatment_list_view")
        action['domain'] = [('study_id', '=', self.id)]
        action['context'] = {'default_parent_id': self.id}
        action['search_view_id'] = False
        return action


    def action_populate_risk_sources(self):
        for study in self:
            study._generate_risk_sources()

    def _generate_risk_sources(self):
        RiskSource = self.env['digiit.ebios_rm.risk.source']
        RiskSrc = self.env['digiit.ebios_rm.risk.src']
        all_sources = RiskSrc.search([])

        for study in self:
            existing = RiskSource.search_read(
                [('study_id', '=', study.id)],
                fields=['source_of_risk_id', 'targeted_objectif_id']
            )
            existing_keys = {(r['source_of_risk_id'][0], r['targeted_objectif_id'][0]) for r in existing if r['source_of_risk_id'] and r['targeted_objectif_id']}

            new_vals = []
            for source in all_sources:
                for target in source.target_objective_ids:
                    key = (source.id, target.id)
                    if key in existing_keys:
                        continue  # Skip existing combo
                    new_vals.append({
                        'study_id': study.id,
                        'source_of_risk_id': source.id,
                        'targeted_objectif_id': target.id,
                    })

            if new_vals:
                RiskSource.create(new_vals)

    def dda_xlsx_report_ebios_rm(self):
        return {
            'type': 'ir.actions.act_url',
            'url': f'/ebios_rm/dda/excel/report/{self.id}',
            'target': 'new'
        }

    def dda_docs_report_ebios_rm(self):
        print('word')

    def dda_ppt_report_ebios_rm(self):
        print('ppt')