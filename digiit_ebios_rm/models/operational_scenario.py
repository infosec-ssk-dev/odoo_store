from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import html2plaintext
import json
import logging
import re

_logger = logging.getLogger(__name__)


class OperationalScenario(models.Model):
    _name = 'digiit.ebios_rm.operational.scenario'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'digiit.ebios_rm.incremental.identifier']
    prefix = 'R-'
    related_field = 'study_id'

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_("Etude"),
        ondelete='cascade'
    )
    risk_identifier = fields.Char(
        string='Identifiant du risque'
    )
    risk_source_id = fields.Many2one(
        'digiit.ebios_rm.risk.source',
        domain="[('study_id','=',study_id)]",
        ondelete='cascade',
    )
    risk_source_id_ref = fields.Char(
        string=_('Ref'),
        related='risk_source_id.prefixed_identifier'
    )
    dreaded_event_id = fields.Many2one(
        'digiit.ebios_rm.dreaded.event',
        string=_('Evénement redouté'),
        domain="[('held','=',True),('study_id','=',study_id)]",
        tracking=True,
    )
    direct_attack = fields.Boolean(
        string=_('L\'attaque est-elle directe ou via pip ?'),
        # computed='_compute_is_direct_attack'
    )
    attack_path = fields.Html(
        string=_('Description du mode opératoire'),
    )

    attack_path_text = fields.Text(
        string=_('Description du mode opératoire (texte)'),
        compute='_compute_attack_path_text',
        store=False
    )

    seriousness = fields.Selection(
        string=_('Gravité'),
        related='strategic_sc_id.seriousness',
        help=_("""
    1 - Mineure: Aucun impact opérationnel ni sur les performances de l'activité ni sur la sécurité des personnes et/ou des biens. L'organisation surmontera la situation sans trop de difficultés)____________________
    2 - Moyenne: Dégradation des performances de l'activité sans impact sur la sécurité des personnes et/ou des biens. L'organisation surmontera la situation malgré quelques difficultés)____________________
    3 - Forte: Forte dégradation des performances de l'activité, avec d'éventuels impacts significatifs sur la sécurité des personnes et/ou des biens. L'organisation surmontera la situation avec de sérieuses difficultés (fonctionnement en mode dégradé)____________________
    4 - Critique: Incapacité pour l'organisation d'assurer la totalité ou une partie de son activité, avec d'éventuels impacts graves sur la sécurité des personnes et/ou des biens. L'organisation ne surmontera vraisemblablement pas la situation (sa survie est menacée).""")
    )
    likelihood = fields.Selection(
        [
            ('1', _('1')),
            ('2', _('2')),
            ('3', _('3')),
            ('4', _('4')),
        ],
        string=_('Likelihood'),
        tracking=True,
        help=_("""
    1 - Peu probable: La source de risque a peu de chance d'atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est faible.____________________
    2 - Probable: La source de risque est susceptible d'atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est significative.____________________
    3 - Très probable: La source de risque va probablement atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est élevée.____________________
    4 - Quasi certain: La source de risque va certainement atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est très élevée.""")
    )
    risk_treatment_id = fields.Many2one(
        'digiit.ebios_rm.risk.treatment',
        string=_('Risk treatment'),
        tracking=True,
    )
    stakeholder_id = fields.Many2one(
        'digiit.ebios_rm.stakeholder',
        string=_('PiP'),
        ondelete="cascade"
    )
    strategic_sc_id = fields.Many2one(
        'digiit.ebios_rm.visual.strategic.scenario',
        domain="[('study_id','=',study_id)]",
        ondelete='cascade',
    )
    removed = fields.Boolean(
        string=_('Supprimé?'),
        default=False
    )

    # New fields from your module
    scenario_data = fields.Text(
        string=_('Données du scénario'),
        help='Représentation JSON du scénario'
    )
    phase_ids = fields.One2many(
        'digiit.ebios_rm.operational.scenario.phase',
        'scenario_id',
        string=_('Phases')
    )
    connection_ids = fields.One2many(
        'digiit.ebios_rm.operational.scenario.connection',
        'scenario_id',
        string=_('Connexions')
    )

    mode_operatoire_ids = fields.One2many(
        'digiit.ebios_rm.operational.scenario.mode',
        'scenario_id',
        string="Modes opératoires"
    )

    mode_operatoire_count = fields.Integer(
        compute="_compute_mode_operatoire_count",
        string="Nb modes"
    )

    #capture field for scenario cyberkillchain
    scenario_image = fields.Binary(
        string='Scenario Image',
        attachment=True,
        help='Captured image of the operational scenario cyber kill chain'
    )

    def _compute_mode_operatoire_count(self):
        for rec in self:
            rec.mode_operatoire_count = len(rec.mode_operatoire_ids)

    @api.depends('likelihood', 'dreaded_event_id')
    def _update_treatment(self):
        for record in self:
            record.risk_treatment_id.write({
                'risidual_likelihood': str(int(record.likelihood) * int(record.seriousness))
            })

    @api.depends('risk_source_id', 'dreaded_event_id')
    def _compute_is_direct_attack(self):
        strategic_scenario = []
        for record in self:
            strategic_scenario = self.env['digiit.ebios_rm.visual.strategic.scenario'].search([
                ('dreaded_event_id', '=', record.dreaded_event_id.id),
                ('study_id', '=', record.study_id.id),
                ('risk_source_id', '=', record.risk_source_id.id)
            ])
            if len(strategic_scenario) > 0:
                record.direct_attack = strategic_scenario[0].is_direct

    @api.model_create_multi
    def create(self, vals_list):
        records = super(OperationalScenario, self).create(vals_list)

        # Post message to study
        for record in records:
            if record.study_id:
                record.study_id.message_post(
                    body=f"Scénario opérationnel créé : {record.prefixed_identifier}"
                )

        return records

    def write(self, vals):
        res = super(OperationalScenario, self).write(vals)

        # Post message to study
        for rec in self:
            if rec.study_id:
                rec.study_id.message_post(
                    body=f"Scénario opérationnel modifié : {rec.prefixed_identifier}"
                )

        return res

    def unlink(self):
        # Store info before deletion
        deletion_info = []
        for rec in self:
            if rec.study_id:
                deletion_info.append((rec.study_id, rec.prefixed_identifier))

        res = super(OperationalScenario, self).unlink()

        # Post messages after deletion
        for study, identifier in deletion_info:
            study.message_post(
                body=f"Scénario opérationnel supprimé : {identifier}"
            )

        return res

    def action_open_builder(self):
        """Open the builder for this specific operational scenario."""
        self.ensure_one()

        # Parse the stored scenario_data if available
        scenario_data = {}
        if self.scenario_data:
            try:
                scenario_data = json.loads(self.scenario_data)
            except Exception as e:
                _logger.error("Error parsing scenario_data: %s", str(e))

        strategic_sc_id = self.strategic_sc_id.id if self.strategic_sc_id and self.strategic_sc_id.exists() else False

        # Get scenario details with specific PIP
        scenario_details = self.get_scenario_with_pip_details()

        return {
            'type': 'ir.actions.client',
            'tag': 'digiit_ebios_rm_operational_scenario.view',
            'target': 'current',
            'params': {
                'scenario_id': self.id,
                'study_id': self.study_id.id,
                'strategic_sc_id': strategic_sc_id,
                'direct_attack': self.direct_attack,
                'stakeholder_id': self.stakeholder_id.id if self.stakeholder_id else False,
            },
            'context': {
                'active_id': self.id,
                'active_ids': [self.id],
                'active_model': 'digiit.ebios_rm.operational.scenario',
                'scenario_id': self.id,
                'study_id': self.study_id.id,
                'scenario_name': self.prefixed_identifier,
                'scenario_data': scenario_data,
                'strategic_sc_id': strategic_sc_id,
                'direct_attack': self.direct_attack,
                'stakeholder_id': self.stakeholder_id.id if self.stakeholder_id else False,
                'scenario_details': scenario_details,  # Add this line
            },
            'res_id': self.id,
        }

    def _sync_modes_from_builder_payload(self, scenario_data):
        """Create/replace mode operatoires based on connections grouped by operationalLevel."""
        self.ensure_one()

        # Remove previous modes (simplest + consistent)
        if self.mode_operatoire_ids:
            self.mode_operatoire_ids.unlink()

        phases = scenario_data.get('phases', [])
        connections = scenario_data.get('connections', [])

        # Build a map instanceId -> step info (name + phase)
        step_info_by_instance = {}
        phase_name_by_id = {}

        for ph in phases:
            phase_id = ph.get('id')
            phase_name = ph.get('name')
            phase_name_by_id[phase_id] = phase_name

            for st in ph.get('steps', []):
                inst = str(st.get('instanceId'))
                step_info_by_instance[inst] = {
                    'name': st.get('name'),
                    'phase_id': phase_id,
                    'phase_name': phase_name
                }

        # Group connections by operationalLevel
        conns_by_level = {}
        for c in connections:
            level = c.get('operationalLevel')
            if not level:
                continue
            conns_by_level.setdefault(int(level), []).append(c)

        def _get_all_steps_for_level(level_conns):
            """
            Get ALL step nodes involved in this mode, including parallel/convergent paths.
            Returns them in a reasonable order (topological sort).
            """
            # Build graph
            graph = {}
            in_degree = {}
            all_nodes = set()

            for c in level_conns:
                s = str(c.get('source'))
                t = str(c.get('target'))

                # Only include actual step nodes (not menace/target/actif)
                s_is_step = not s.startswith(("menace-", "target-", "actif-"))
                t_is_step = not t.startswith(("menace-", "target-", "actif-"))

                if s_is_step:
                    all_nodes.add(s)
                    graph.setdefault(s, [])
                    in_degree.setdefault(s, 0)

                if t_is_step:
                    all_nodes.add(t)
                    in_degree.setdefault(t, 0)

                # Add edge only between steps
                if s_is_step and t_is_step:
                    graph[s].append(t)
                    in_degree[t] = in_degree.get(t, 0) + 1

            # Topological sort (Kahn's algorithm) to get ordered steps
            queue = [node for node in all_nodes if in_degree.get(node, 0) == 0]
            ordered_steps = []

            while queue:
                # Sort queue to ensure consistent ordering
                queue.sort()
                node = queue.pop(0)
                ordered_steps.append(node)

                for neighbor in graph.get(node, []):
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)

            return ordered_steps

        # Create mode records and collect all mode HTMLs for attack_path
        all_modes_html = []

        for lvl in sorted(conns_by_level.keys()):
            level_conns = conns_by_level[lvl]
            ordered_step_ids = _get_all_steps_for_level(level_conns)

            # Group steps by phase to organize the description
            steps_by_phase = {}
            for node_id in ordered_step_ids:
                step_info = step_info_by_instance.get(node_id)
                if step_info:
                    phase_id = step_info['phase_id']
                    if phase_id not in steps_by_phase:
                        steps_by_phase[phase_id] = {
                            'phase_name': step_info['phase_name'],
                            'steps': []
                        }
                    steps_by_phase[phase_id]['steps'].append(step_info['name'])

            # Build HTML with phase as bullet + steps ordered
            html = "<div class='attack-path'>"
            html += "<ul class='attack-path-phases'>"

            #uncomment this part if your want the phase names in the description
            # for phase_id in sorted(steps_by_phase.keys()):
            #     phase_data = steps_by_phase[phase_id]
            #     html += "<li>"
            #     html += f"<strong>{phase_data['phase_name']}</strong>"
            #     html += "<ol>"
            #     for step_name in phase_data['steps']:
            #         html += f"<li>{step_name}</li>"
            #     html += "</ol>"
            #     html += "</li>"

            #and comment this part for phase names in the description
            for phase_id in sorted(steps_by_phase.keys()):
                phase_data = steps_by_phase[phase_id]
                for step_name in phase_data['steps']:
                    html += f"<li>{step_name}</li>"

            html += "</ul>"
            html += "</div>"

            # Create mode record
            self.env['digiit.ebios_rm.operational.scenario.mode'].create({
                'scenario_id': self.id,
                'name': f"Mode opératoire {lvl}",
                'sequence': lvl * 10,
                'mode_level': lvl,
                'description_html': html,
                'mode_data': json.dumps({
                    'operationalLevel': lvl,
                    'ordered_nodes': ordered_step_ids,
                    'connections': level_conns,
                }),
            })

            # Add this mode to the combined attack path
            all_modes_html.append(f"<div class='mode-section'><h6>Mode opératoire {lvl}</h6>{html}</div>")

        # Update the scenario's attack_path with all modes
        if all_modes_html:
            combined_attack_path = "<div class='all-modes'>" + "".join(all_modes_html) + "</div>"
            self.write({'attack_path': combined_attack_path})

    @api.model
    def create_from_builder(self, scenario_data):
        """
        Create or update an operational scenario from the builder interface data.

        :param scenario_data: Dictionary containing scenario details (name, phases, connections, etc.)
        :return: ID of the created or updated scenario
        """
        try:
            # Validate input data
            if not scenario_data or not isinstance(scenario_data, dict):
                raise UserError(_('Données de scénario non valides fournies: %s') % str(scenario_data))

            if not scenario_data.get('name'):
                raise UserError(_('Scenario name is required'))

            if not scenario_data.get('study_id'):
                raise UserError(_('L\'ID de l\'étude est necessaire pour la création des scenarios'))

            # Prepare scenario values
            scenario_id = scenario_data.get('id', False)
            scenario_vals = {
                'prefixed_identifier': scenario_data['name'],  # Map frontend 'name' to 'prefixed_identifier'
                'scenario_data': json.dumps({
                    'phases': scenario_data.get('phases', []),
                    'connections': scenario_data.get('connections', []),
                }),
                'study_id': scenario_data.get('study_id', False),
            }

            # Only set strategic_sc_id if it's a valid ID
            strategic_sc_id = scenario_data.get('strategic_sc_id', False)
            if strategic_sc_id:
                # Check if the strategic scenario exists
                strategic_sc = self.env['digiit.ebios_rm.visual.strategic.scenario'].browse(strategic_sc_id)
                if strategic_sc.exists():
                    scenario_vals['strategic_sc_id'] = strategic_sc_id
                else:
                    _logger.warning(f"Strategic scenario with ID {strategic_sc_id} not found. Not setting relation.")

            # Create or update scenario
            if scenario_id:
                scenario = self.browse(scenario_id)
                if not scenario.exists():
                    raise UserError(_('Scénario avec l\'ID %s introuvable') % scenario_id)

                # To prevent constraint errors, handle relations carefully
                # First clear any problematic relations
                if 'strategic_sc_id' in scenario_vals and scenario_vals[
                    'strategic_sc_id'] != scenario.strategic_sc_id.id:
                    scenario.write({'strategic_sc_id': False})

                scenario.write(scenario_vals)
            else:
                scenario = self.create(scenario_vals)

            # Clear existing phases and connections
            if scenario.phase_ids:
                scenario.phase_ids.unlink()
            if scenario.connection_ids:
                scenario.connection_ids.unlink()

            # Build phases, steps, and connections
            phase_mapping = {}
            step_instance_mapping = {}

            # Create phases and steps
            for phase_data in scenario_data.get('phases', []):
                phase = self.env['digiit.ebios_rm.operational.scenario.phase'].create({
                    'scenario_id': scenario.id,
                    'name': phase_data['name'],
                    'sequence': phase_data['id'],
                })
                phase_mapping[phase_data['id']] = phase.id

                for step_data in phase_data.get('steps', []):
                    elementary_step = self.env['digiit.ebios_rm.elementary.step'].browse(step_data.get('elementaryStepId'))
                    step = self.env['digiit.ebios_rm.operational.scenario.step'].create({
                        'phase_id': phase.id,
                        'name': elementary_step.name if elementary_step.exists() else step_data['name'],
                        'elementary_step_id': elementary_step.id if elementary_step.exists() else False,
                    })
                    step_instance_mapping[step_data['instanceId']] = step.id

            # Create connections
            for conn_data in scenario_data.get('connections', []):
                if conn_data['source'] in step_instance_mapping and conn_data['target'] in step_instance_mapping:
                    self.env['digiit.ebios_rm.operational.scenario.connection'].create({
                        'scenario_id': scenario.id,
                        'source_step_id': step_instance_mapping[conn_data['source']],
                        'target_step_id': step_instance_mapping[conn_data['target']],
                        'connection_type': conn_data['type'],
                    })
                else:
                    _logger.warning(
                        "Skipping connection: source %s or target %s not found in step_instance_mapping",
                        conn_data['source'], conn_data['target']
                    )

            # Generate a simple attack path description
            steps_description = []
            for phase_data in scenario_data.get('phases', []):
                for step_data in phase_data.get('steps', []):
                    if step_data.get('name'):
                        steps_description.append(step_data['name'])

            # Create simple HTML for the attack path
            attack_path_html = '<div class="attack-path"><ol>'
            for step_name in steps_description:
                attack_path_html += f'<li>{step_name}</li>'
            attack_path_html += '</ol></div>'

            # Update the scenario with the attack path
            if steps_description:
                #scenario.write({'attack_path': attack_path_html})
                scenario._sync_modes_from_builder_payload(scenario_data)

            return scenario.id

        except Exception as e:
            _logger.error("Error in create_from_builder: %s, Data: %s", str(e), scenario_data)
            raise UserError(_('Échec de l\'enregistrement du scénario opérationnel:') % str(e))

    @api.depends('attack_path')
    def _compute_attack_path_text(self):
        for record in self:
            if record.attack_path:
                # Convert HTML to plain text while preserving line breaks
                text = record.attack_path
                # Replace <br> and </p> tags with newlines before converting
                text = re.sub(r'<br\s*/?>', '\n', text)
                text = re.sub(r'</p>', '\n', text)
                text = re.sub(r'</li>', '\n', text)
                text = re.sub(r'</div>', '\n', text)
                # Convert to plain text
                text = html2plaintext(text)
                # Clean up multiple consecutive newlines
                text = re.sub(r'\n\s*\n', '\n', text)
                record.attack_path_text = text.strip()
            else:
                record.attack_path_text = ''

    def action_delete_sc_op_with_confirmation(self):
        """
        Delete the current record(s) after confirmation.
        This method simply calls unlink() to remove the record(s).
        """
        self.unlink()
        return True

    def action_open_modes(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Modes opératoires',
            'res_model': 'digiit.ebios_rm.operational.scenario.mode',
            'view_mode': 'list,form',
            'domain': [('scenario_id', '=', self.id)],
            'context': {'default_scenario_id': self.id},
        }

    def get_scenario_with_pip_details(self):
        """
        Get scenario details including the specific PIP and its CORRECT SCORE for this operational scenario.
        """
        self.ensure_one()

        # Get the base strategic scenario details
        if self.strategic_sc_id:
            base_details = self.strategic_sc_id.get_scenario_details(self.strategic_sc_id.id)

            # If this operational scenario has a specific PIP, update the connection
            if self.stakeholder_id and base_details.get('connections'):
                # Find the ecosystem for this specific PIP in this study to get the correct score
                ecosystem = self.env['digiit.ebios_rm.ecosystem'].search([
                    ('study_id', '=', self.study_id.id),
                    ('stakeholder_id', '=', self.stakeholder_id.id)
                ], limit=1)

                # Get the score from the ecosystem, default to 0 if not found
                pip_score = ecosystem.threat_level if ecosystem else 0

                for connection in base_details['connections']:
                    # Update the stakeholder in the connection with this scenario's specific PIP and score
                    if 'stakeholder' in connection:
                        connection['stakeholder'] = {
                            'name': self.stakeholder_id.name,
                            'logo_url': self.stakeholder_id.icon,
                            'score': pip_score,
                        }

            return base_details

        return {'connections': []}

class ElementaryStep(models.Model):
    _name = 'digiit.ebios_rm.elementary.step'
    _description = 'Etape élementaire'
    _order = 'name'

    name = fields.Char(
        string='Nom de l\'étape',
        required=True
    )
    description = fields.Text(
        string='Description'
    )
    phase = fields.Selection([
        ('connaitre', 'Connaître'),
        ('rentrer', 'Rentrer'),
        ('trouver', 'Trouver'),
        ('exploiter', 'Exploiter')
    ], string='Phase', required=True, help="La phase à laquelle appartient cette étape")
    risk_level = fields.Selection([
        ('low', 'Faible'),
        ('medium', 'Medium'),
        ('high', 'Elevée'),
        ('critical', 'Critique')
    ], string='Niveau de risque', default='medium')
    reference_scenarios = fields.One2many(
        'digiit.ebios_rm.operational.scenario.step',
        'elementary_step_id',
        string='Utilisé dans les scénarios'
    )


class OperationalScenarioPhase(models.Model):
    _name = 'digiit.ebios_rm.operational.scenario.phase'
    _description = 'Phase sc. opérationnel'
    _order = 'sequence'

    name = fields.Char(
        string='Nom',
        required=True
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10
    )
    scenario_id = fields.Many2one(
        'digiit.ebios_rm.operational.scenario',
        string='Scénario',
        required=True,
        ondelete='cascade'
    )
    step_ids = fields.One2many(
        'digiit.ebios_rm.operational.scenario.step',
        'phase_id',
        string='Etapes'
    )


class OperationalScenarioStep(models.Model):
    _name = 'digiit.ebios_rm.operational.scenario.step'
    _description = 'Etape Sc. opérationnel'

    name = fields.Char(
        string='Nom',
        required=True
    )
    phase_id = fields.Many2one(
        'digiit.ebios_rm.operational.scenario.phase',
        string='Phase',
        required=True,
        ondelete='cascade'
    )
    scenario_id = fields.Many2one(
        'digiit.ebios_rm.operational.scenario',
        string='Scénario',
        store=True
    )
    elementary_step_id = fields.Many2one(
        'digiit.ebios_rm.elementary.step',
        string='Etape élementaire',
        help='Réference vers les étapes élémentaires prédefinies'
    )
    outgoing_connection_ids = fields.One2many(
        'digiit.ebios_rm.operational.scenario.connection',
        'source_step_id',
        string='Connexions sortantes'
    )
    incoming_connection_ids = fields.One2many(
        'digiit.ebios_rm.operational.scenario.connection',
        'target_step_id',
        string='Connexions entrantes'
    )


class OperationalScenarioConnection(models.Model):
    _name = 'digiit.ebios_rm.operational.scenario.connection'
    _description = 'Connecxion sc. opérationnel'

    scenario_id = fields.Many2one(
        'digiit.ebios_rm.operational.scenario',
        string='Scénario',
        required=True,
        ondelete='cascade'
    )
    source_step_id = fields.Many2one(
        'digiit.ebios_rm.operational.scenario.step',
        string='Etapes de source',
        required=True,
        ondelete='cascade'
    )
    target_step_id = fields.Many2one(
        'digiit.ebios_rm.operational.scenario.step',
        string='Etape cible',
        required=True,
        ondelete='cascade'
    )
    connection_type = fields.Selection([
        ('serial', 'Serial'),
        ('parallel', 'Parallelle')
    ], string='Type de connexion', required=True, default='serial')


class OperationalScenarioMode(models.Model):
    _name = 'digiit.ebios_rm.operational.scenario.mode'
    _description = 'Mode opératoire'
    _order = 'sequence, id'

    scenario_id = fields.Many2one(
        'digiit.ebios_rm.operational.scenario',
        required=True,
        ondelete='cascade'
    )

    scenario_identifier = fields.Char(
        string="Scénario",
        related="scenario_id.prefixed_identifier",
        store=True,
        readonly=True
    )

    name = fields.Char(required=True, default="Mode opératoire")
    sequence = fields.Integer(default=10)

    description_html = fields.Html(string="Description")
    description_text = fields.Text(
        compute="_compute_description_text",
        store=False
    )

    mode_level = fields.Integer(string="Niveau (visuel)", help="Operational level from builder")

    # optional: store extracted data (for debug / future)
    mode_data = fields.Text(string="Mode data (JSON)")

    def _compute_description_text(self):
        for rec in self:
            rec.description_text = html2plaintext(rec.description_html or "")
