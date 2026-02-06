from odoo import models, fields, _, api


class RiskSource(models.Model):
    _name = 'digiit.ebios_rm.risk.source'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'digiit.ebios_rm.incremental.identifier']

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_("Etude"),
        ondelete='cascade'
    )

    name = fields.Char(string=_('Nom'), compute="_calculate_name", readonly=True, store=True)

    prefix = 'SR-OV-'
    related_field = 'study_id'

    dreaded_event_ids = fields.Many2many(
        'digiit.ebios_rm.dreaded.event',
        'risk_source_id',
        domain="[('held','=',True),('study_id','=',study_id)]",
        string=_('Evénements redoutés associés'),
    )

    operational_scenario_id = fields.Many2one(
        'digiit.ebios_rm.operational.scenario',
        ondelete='cascade',
    )

    targeted_objectif_id = fields.Many2one(
        'digiit.ebios_rm.target.objective',
        string=_('Objectif visé'),
        # domain="[('id','in',self.source_of_risk_id.target_objective_ids.ids)]",
        required=True,
    )
    targeted_objectif_name = fields.Char(related='targeted_objectif_id.name', store=True)

    source_of_risk_id = fields.Many2one(
        'digiit.ebios_rm.risk.src',
        string=_('Source de risque'),
        required=True,
    )
    source_of_risk_name = fields.Char(related='source_of_risk_id.name', store=True)

    motivation_id = fields.Many2one(
        'digiit.ebios_rm.motivation',
        string=_('Motivation'),
    )

    ressource_id = fields.Many2one(
        'digiit.ebios_rm.ressource',
        string=_('Ressources'),
    )

    activity_id = fields.Many2one(
        'digiit.ebios_rm.activity',
        string=_('Activities'),
    )

    pertinence_id = fields.Many2one(
        'digiit.ebios_rm.pertinence',
        string=_('Pertinence Proposée'),
        compute='_compute_pertinence',
        store=True,
        help=_('Proposed Pertinence based on the Motivation and Ressource of this Risk Source.')
    )
    pertinence_retenue_id = fields.Many2one(
        'digiit.ebios_rm.pertinence',
        string=_('Pertinence Retenue'),
        help = _('Pertinence retained. You may override the automatically calculated value.')
    )

    #held depends on pertinence_retenue_id
    held = fields.Boolean(
        string=_('Retenue'),
        compute='_compute_held',
        store=True,
    )

    display_name = fields.Char(compute='_compute_display_name', store=True)

    justification = fields.Text(string=_('Justification'))
    description = fields.Text(string=_('Description'))

    strategic_scenario_count = fields.Integer(
        string=_('Nombre de scénarios stratégiques'),
        compute='_compute_strategic_scenario_count',
        store=True
    )

    @api.depends('source_of_risk_id', 'targeted_objectif_id')
    def _compute_display_name(self):

        for rec in self:
            risk_name = rec.source_of_risk_id.name if rec.source_of_risk_id else ""  # Handle cases where risk is not set
            target_name = rec.targeted_objectif_id.name if rec.targeted_objectif_id else ""  # Handle cases where target is not set
            rec.display_name = f"{risk_name} / {target_name}" if risk_name and target_name else (
                        risk_name or target_name or "")

            # def _compute_name(self):

    #      for record in self:
    #         record.name = f"{record.prefixed_identifier} {record.risk_src} / {record.targeted_objective}"

    @api.depends('study_id')
    def _compute_strategic_scenario_count(self):
        for record in self:
            record.strategic_scenario_count = self.env['digiit.ebios_rm.visual.strategic.scenario'].search_count([
                ('risk_source_id', '=', record.id)
            ])

    # NEW: Separate compute method for held based on pertinence_retenue_id
    # Falls back to pertinence_id (proposed) if retenue is not set
    @api.depends('pertinence_retenue_id', 'pertinence_id')
    def _compute_held(self):
        for record in self:
            # Use retenue if available, otherwise fallback to proposed
            pertinence = record.pertinence_retenue_id or record.pertinence_id

            if pertinence:
                record.held = pertinence.retenu
            else:
                record.held = False

    @api.depends('motivation_id', 'ressource_id')
    def _compute_pertinence(self):
        pertinence_matrix = self.env['digiit.ebios_rm.pertinence.matrix']

        for record in self:
            # If missing one field → reset proposed pertinence
            if not record.motivation_id or not record.ressource_id:
                record.pertinence_id = False
                # Only fill pertinence_retenue if it was previously empty
                if not record.pertinence_retenue_id:
                    record.pertinence_retenue_id = False
                continue

            # Look up matrix result
            matrix_entry = pertinence_matrix.search([
                ('motivation_id', '=', record.motivation_id.id),
                ('ressource_id', '=', record.ressource_id.id)
            ], limit=1)

            if matrix_entry and matrix_entry.pertinence_id:
                proposed = matrix_entry.pertinence_id
                record.pertinence_id = proposed

                # NEVER overwrite user retained choice
                # Only fill it IF empty AND there is a valid proposed
                if not record.pertinence_retenue_id:
                    record.pertinence_retenue_id = proposed
                # held will be computed automatically by _compute_held

            else:
                # No mapping found
                record.pertinence_id = False
                # Only clear pertinence_retenue if it was previously empty
                if not record.pertinence_retenue_id:
                    record.pertinence_retenue_id = False

    def _calculate_name(self):
        for record in self:
            record.name = f"{record.prefixed_identifier} {record.source_of_risk_id.name or ''} / {record.targeted_objectif_id.name or ''}"
