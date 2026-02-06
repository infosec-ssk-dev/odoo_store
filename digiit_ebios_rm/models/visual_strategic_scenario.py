from odoo import models, fields, api, _


class Menace(models.Model):
    _name = 'digiit.ebios_rm.risk.menace'
    _description = 'Menace'
    name = fields.Char(string='Nom', required=True)
    er = fields.Char(string='Evénement redouté', required=True)
    icon = fields.Image(string='Icône')
    objectif = fields.Char(string='Objectif Visé')
    gravity_level = fields.Char(string="Gravité")
    color = fields.Char(string=_('Couleur'))

class Actif(models.Model):
    _name = 'digiit.ebios_rm.risk.actif'
    _description = 'Actif'
    name = fields.Char(string='Nom', required=True)
    icon = fields.Image(string='Icône')


class Scenario(models.Model):
    _name = 'digiit.ebios_rm.risk.scenario'
    _description = 'Scénario'

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_("Etude"),
        ondelete='cascade'
    )
    menace_id = fields.Many2one('digiit.ebios_rm.risk.menace', string='Menace', required=True)
    actif_id = fields.Many2one('digiit.ebios_rm.risk.actif', string='Actif', required=True)
    pp_id = fields.Many2one('digiit.ebios_rm.risk.pp', string='Partie Prenante')
    is_direct_attack = fields.Boolean(string='Attaque Directe', compute='_compute_is_direct_attack', store=True)
    description = fields.Char(string='Description')
    damage = fields.Char(string='Dommage')

    @api.depends('pp_id')
    def _compute_is_direct_attack(self):
        for record in self:
            record.is_direct_attack = not record.pp_id

    @api.model
    def get_scenario_details(self, scenario_id):
        scenario = self.browse(scenario_id)
        if not scenario:
            return False

        connections = []
        gravity_level = scenario.menace_id.gravity_level or 'Medium'
        gravity_color = scenario.menace_id.color or '#FFA500'

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        menace_icon_url = f"{base_url}/web/image/digiit.ebios_rm.risk.menace/{scenario.menace_id.id}/icon" if scenario.menace_id.icon else '/digiit_ebios_rm/static/src/img/threat_icon.png'
        asset_icon_url = f"{base_url}/web/image/digiit.ebios_rm.risk.actif/{scenario.actif_id.id}/icon" if scenario.actif_id.icon else '/digiit_ebios_rm/static/src/img/asset_icon.png'

        connection = {
            "id": f"connection-{scenario.id}",
            "threat": {
                "name": scenario.menace_id.name,
                "er": scenario.menace_id.er,
                "icon_url": menace_icon_url,
                "objectif": scenario.menace_id.objectif,
                "gravity_level": gravity_level,
                "color": gravity_color,
            },
            "asset": {
                "name": scenario.actif_id.name,
                "icon_url": asset_icon_url,
            },
            "objectif": scenario.description or 'Attack scenario',
            "gravity_level": gravity_level,
            "gravity_color": gravity_color,
            "scenarios": [{
                "id": scenario.id,
                "study_id": scenario.study_id.id,
                "menace_id": scenario.menace_id.id,
                "actif_id": scenario.actif_id.id,
                "pp_id": scenario.pp_id.id if scenario.pp_id else False,
                "is_direct_attack": scenario.is_direct_attack,
                "description": scenario.description,
                "damage": scenario.damage,
            }]
        }

        if scenario.pp_id:
            pp_icon_url = f"{base_url}/web/image/digiit.ebios_rm.risk.pp/{scenario.pp_id.id}/logo" if scenario.pp_id.logo else '/digiit_ebios_rm/static/src/img/pp_icon.png'
            connection["stakeholder"] = {
                "name": scenario.pp_id.name,
                "logo_url": pp_icon_url,
                "score": scenario.pp_id.score,
            }

        connections.append(connection)
        return {"connections": connections}

class PartiesPrenantes(models.Model):
    _name = 'digiit.ebios_rm.risk.pp'
    _description = 'Parties Prenantes'

    name = fields.Char(string='Nom', required=True)
    logo = fields.Image(string='Logo')
    score = fields.Float(string='Score', default=0)


class VisualStrategicScenario(models.Model):
    _name = 'digiit.ebios_rm.visual.strategic.scenario'
    _description = 'Scénario stratégique visuel'

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_("Etude"),
        ondelete='cascade'
    )
    risk_source_id = fields.Many2one(
        'digiit.ebios_rm.risk.source',
        string=_('SR-OV'),
        domain="[('held','=',True),('study_id','=',study_id)]",
        ondelete='cascade',
        required=True,
    )
    dreaded_event_id = fields.Many2one(
        'digiit.ebios_rm.dreaded.event',
        string=_('Evénement redouté'),
        domain="[('held','=',True),('study_id','=',study_id)]",
        ondelete='cascade',
        required=True
    )

    stakeholder_ids = fields.Many2many(
        'digiit.ebios_rm.stakeholder',
        'visual_sc_stakeholder_rel',  # <-- short table name
        'visual_sc_id',  # left key (VisualStrategicScenario)
        'stakeholder_id',  # right key
        string=_('PiP'),
        domain="[('id', 'in', available_stakeholder_ids)]",
        ondelete="cascade"
    )

    # Computed field to get available stakeholders from the study's ecosystems
    available_stakeholder_ids = fields.Many2many(
        'digiit.ebios_rm.stakeholder',
        compute='_compute_available_stakeholders',
        store=False
    )

    operational_scenario_count = fields.Integer(
        string=_('Nombre de scénarios opérationnels'),
        compute='_compute_operational_scenario_count',
        store=True
    )

    risk_treatment_count = fields.Integer(
        string=_('Nombre de traitements de risque'),
        compute='_compute_risk_treatment_count',
        store=True
    )

    damage = fields.Char(
        string=_('Dommage'),
        related='dreaded_event_id.name'
    )

    actif = fields.Many2one(
        'res.company',
        string=_('Actif'),
        related='study_id.company_id',
        ondelete="cascade"
    )


    actif_display = fields.Char(
        string=_('Cible'),
        compute='_compute_actif_display'
    )

    justification = fields.Text(
        string=_('Justification de gravité'),
        tracking=True
    )


    @api.model_create_multi
    def create(self, vals_list):
        tag_directe = self.env['digiit.ebios_rm.tag'].search([('name', '=', 'Directe')])
        if not tag_directe:
            tag_directe = self.env['digiit.ebios_rm.tag'].create({'name': 'Directe'})
        else:
            tag_directe = tag_directe[0]

        for vals in vals_list:
            vals['direct'] = [tag_directe.id]

        records = super().create(vals_list)

        for rec in records:
            if rec.study_id:
                scenario_name = f"{rec.risk_source_id.source_of_risk_id.name} → {rec.dreaded_event_id.name}"
                rec.study_id.message_post(
                    body=f"Scénario stratégique créé : {scenario_name}"
                )

        return records

    def write(self, vals):
        res = super().write(vals)

        for rec in self:
            if rec.study_id:
                scenario_name = f"{rec.risk_source_id.source_of_risk_id.name} → {rec.dreaded_event_id.name}"
                rec.study_id.message_post(
                    body=f"Scénario stratégique modifié : {scenario_name}"
                )

        return res

    def unlink(self):
        deletion_info = []
        for rec in self:
            if rec.study_id:
                scenario_name = f"{rec.risk_source_id.source_of_risk_id.name} → {rec.dreaded_event_id.name}"
                deletion_info.append((rec.study_id, scenario_name))

        res = super().unlink()

        for study, scenario_name in deletion_info:
            study.message_post(
                body=f"Scénario stratégique supprimé : {scenario_name}"
            )

        return res


    @api.depends('study_id')
    def _compute_operational_scenario_count(self):
        for record in self:
            record.operational_scenario_count = self.env['digiit.ebios_rm.operational.scenario'].search_count([
                ('strategic_sc_id', '=', record.id)
            ])

    @api.depends('study_id')
    def _compute_risk_treatment_count(self):
        for record in self:
            op_scenarios = self.env['digiit.ebios_rm.operational.scenario'].search([
                ('strategic_sc_id', '=', record.id)
            ])
            record.risk_treatment_count = self.env['digiit.ebios_rm.risk.treatment'].search_count([
                ('risk_id', 'in', op_scenarios.ids)
            ])

    @api.depends('study_id', 'study_id.ecosystem_ids')
    def _compute_available_stakeholders(self):
        for record in self:
            if record.study_id:
                # Get all stakeholders from ecosystems in this study
                stakeholder_ids = record.study_id.ecosystem_ids.mapped('stakeholder_id').ids
                record.available_stakeholder_ids = stakeholder_ids
            else:
                record.available_stakeholder_ids = []


    @api.depends('study_id', 'study_id.is_for_partner', 'study_id.partner', 'study_id.company_id')
    def _compute_actif_display(self):
        for record in self:
            if record.study_id.is_for_partner and record.study_id.partner:
                record.actif_display = record.study_id.partner.name
            else:
                record.actif_display = record.study_id.company_id.name

    direct = fields.Many2many(
        'digiit.ebios_rm.tag',
        store=False,
        readonly=True
    )

    seriousness = fields.Selection([
        ('1', _("1")),
        ('2', _("2")),
        ('3', _("3")),
        ('4', _("4")),
    ], string=_('Gravité'), readonly=False, store=True)

    def _is_held(self):
        self.ensure_one()
        param = self.env['ir.config_parameter'].sudo().get_param('digiit_ebios_rm.accepted_risk_level_sc_strategic')
        try:
            return float(param) <= float(self.seriousness)
        except (ValueError, TypeError):
            return False

    def build_grafical_elements(self):
        limit_values = self.env['digiit.ebios_rm.threat.level.limit'].search([], order='value DESC, is_minimal DESC')

        for record in self:
            strategic_scs = self.env['digiit.ebios_rm.visual.strategic.scenario'].search([
                ('study_id', '=', record.study_id.id),
                ('risk_source_id', '=', record.risk_source_id.id),
                ('dreaded_event_id', '=', record.dreaded_event_id.id),
            ])
            if len(strategic_scs) == 0:
                continue

            color = ''
            for limit in limit_values:
                if int(record.seriousness) > limit.value:
                    color = limit.color
                    if limit.is_maximal:
                        break
                elif limit.is_minimal:
                    color = limit.color
                    break

            menaces = self.env['digiit.ebios_rm.risk.menace'].search([
                ('name', '=', record.risk_source_id.source_of_risk_id.name),
                ('er', '=', record.dreaded_event_id.name),
                ('objectif', '=', record.risk_source_id.targeted_objectif_id.name)
            ])
            if len(menaces) > 0:
                menace = menaces[0]
                menaces.write({
                    'gravity_level': record.seriousness,
                    'icon': record.risk_source_id.source_of_risk_id.image,
                    'color': color
                })
            else:
                menace = self.env['digiit.ebios_rm.risk.menace'].create({
                    'name': record.risk_source_id.source_of_risk_id.name,
                    'er': record.dreaded_event_id.name,
                    'objectif': record.risk_source_id.targeted_objectif_id.name,
                    'icon': record.risk_source_id.source_of_risk_id.image,
                    'gravity_level': record.seriousness,
                    'color': color
                })

            actif_name = record.study_id.partner.name if record.study_id.is_for_partner else record.study_id.company_id.name
            actifs = self.env['digiit.ebios_rm.risk.actif'].search([('name', '=', actif_name)])
            if len(actifs) > 0:
                actif = actifs[0]
                actif.write({
                    'icon': record.study_id.partner.image_1920 if record.study_id.is_for_partner else record.study_id.company_id.logo
                })
            else:
                actif = self.env['digiit.ebios_rm.risk.actif'].create({
                    'name': actif_name,
                    'icon': record.study_id.partner.image_1920 if record.study_id.is_for_partner else record.study_id.company_id.logo,
                })

            # CHECK FOR EXISTING DIRECT SCENARIO BEFORE CREATING
            existing_direct_scenario = self.env['digiit.ebios_rm.risk.scenario'].search([
                ('study_id', '=', record.study_id.id),
                ('menace_id', '=', menace.id),
                ('actif_id', '=', actif.id),
                ('pp_id', '=', False)  # Direct attack has no PP
            ])

            if not existing_direct_scenario:
                self.env['digiit.ebios_rm.risk.scenario'].create({
                    'study_id': record.study_id.id,
                    'menace_id': menace.id,
                    'actif_id': actif.id,
                    'damage': record.dreaded_event_id.name,
                    # 'description': strategic_scs[0].description,
                })

            ecosystem_dictionary = {}
            ecosystems = self.env['digiit.ebios_rm.ecosystem'].search([('study_id', '=', record.study_id.id)])
            for ecosystem in ecosystems:
                #commented that only pip that are held should be displayed in graphic
                # if ecosystem.is_retenu():
                ecosystem_dictionary[ecosystem.stakeholder_id.name] = ecosystem

            for stakeholder in record.stakeholder_ids:
                pps = self.env['digiit.ebios_rm.risk.pp'].search([('name', '=', stakeholder.name)])

                # get the score. If the stakeholder is not in the dictionary, the score is 0.
                ecosystem_for_pip = ecosystem_dictionary.get(stakeholder.name)
                pip_score = ecosystem_for_pip.threat_level if ecosystem_for_pip else 0

                if len(pps) > 0:
                    pp = pps[0]
                    pp.write({
                        'logo': stakeholder.icon,
                        'score': pip_score,
                    })
                else:
                    pp = self.env['digiit.ebios_rm.risk.pp'].create({
                        'name': stakeholder.name,
                        'logo': stakeholder.icon,
                        'score': pip_score,
                    })

                # CHECK FOR EXISTING INDIRECT SCENARIO BEFORE CREATING
                existing_indirect_scenario = self.env['digiit.ebios_rm.risk.scenario'].search([
                    ('study_id', '=', record.study_id.id),
                    ('menace_id', '=', menace.id),
                    ('actif_id', '=', actif.id),
                    ('pp_id', '=', pp.id)
                ])

                if not existing_indirect_scenario:
                    self.env['digiit.ebios_rm.risk.scenario'].create({
                        'study_id': record.study_id.id,
                        'menace_id': menace.id,
                        'actif_id': actif.id,
                        'pp_id': pp.id,
                        'damage': record.dreaded_event_id.name,
                        'description': ecosystem_dictionary.get(stakeholder.name,
                                                                ecosystem).description
                    })

    @api.model
    def get_scenario_details(self, scenario_id):
        scenario = self.browse(scenario_id)
        if not scenario.exists():
            return {'connections': []}

        # Rebuild graphical elements to ensure fresh data
        scenario.build_grafical_elements()

        risk_scenarios = self.env['digiit.ebios_rm.risk.scenario'].search([
            ('study_id', '=', scenario.study_id.id),
            ('menace_id.name', '=', scenario.risk_source_id.source_of_risk_id.name),
            ('menace_id.er', '=', scenario.dreaded_event_id.name),
        ])

        connections = []
        for risk_scenario in risk_scenarios:
            risk_details = risk_scenario.get_scenario_details(risk_scenario.id)
            if risk_details and risk_details.get('connections'):
                connections.extend(risk_details['connections'])

        return {'connections': connections}

    def action_delete_sc_st_with_confirmation(self):
        """
        Delete the current record(s) after confirmation.
        This method simply calls unlink() to remove the record(s).
        """
        self.unlink()
        return True