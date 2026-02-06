from odoo import models, fields, _, api


class BusinessValue(models.Model):
    _name = 'digiit.ebios_rm.business.value'
    _description = _('Valeur métier')
    _inherit = ['mail.thread', 'mail.activity.mixin', 'digiit.ebios_rm.incremental.identifier']

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_("Etude"),
        ondelete='cascade'
    )

    name = fields.Char(string=_('Dénomination'), tracking=True, required=True)
    type = fields.Selection(
        [
            ('process', _("Processus")),
            ('information', _("Information"))
        ],
        string=_("Nature"),
        default="process",
        required=True
    )

    # Manageable
    responsible_entity_person = fields.Many2one(
        'res.partner',
        string=_('Entité/ personne responsable'),
        domain="[('parent_id', '=', concerned_partner_id)]"
    )
    concerned_partner_id = fields.Many2one(
        'res.partner',
        string='Partenaire concerné',
        compute='_compute_concerned_partner_bv'
    )

    description = fields.Html(string=_("Description"))

    @api.depends('study_id', 'study_id.is_for_partner', 'study_id.partner', 'study_id.company_id')
    def _compute_concerned_partner_bv(self):
        for record in self:
            if record.study_id:
                if record.study_id.is_for_partner and record.study_id.partner:
                    record.concerned_partner_id = record.study_id.partner
                elif record.study_id.company_id and record.study_id.company_id.partner_id:
                    record.concerned_partner_id = record.study_id.company_id.partner_id
                else:
                    record.concerned_partner_id = False
            else:
                record.concerned_partner_id = False

    # fields.Selection(
    #     selection=lambda self: self._get_employee_selection(),
    #     string=_('Entité/personne résponsable'),
    #     required=False,
    # )

    local_ids = fields.Many2many(
        'digiit.ebios_rm.local',
        relation='bv_local',
        column1='bv_id',
        column2='local_id',
        string=_('Valeurs métiers'), required=True
    )

    application_ids = fields.Many2many(
        'digiit.ebios_rm.application',
        relation='bv_application',
        column1='bv_id',
        column2='application_id',
        string=_('Application / Software')
    )

    material_paper_ids = fields.One2many(
        'digiit.ebios_rm.material.paper',
        'business_value_id',
        string=_('Matériels / Papiers'),
    )

    network_ids = fields.Many2many(
        'digiit.ebios_rm.network',
        relation='bv_network',
        column1='bv_id',
        column2='network_id',
        string=_('Réseaux'),
    )

    company_id = fields.Many2one(
        'res.company',
        string=_('Société'),
        required=True,
        default=lambda self: self.env.company.id,
        tracking=True,
    )

    stakeholder_ids = fields.Many2many(
        comodel_name='digiit.ebios_rm.stakeholder',
        relation='business_value_stakeholder_rel',
        domain="[('is_internal','=',False)]",
        column1='bv_id',
        column2='stakeholder_id',
        string=_('Parties prenantes')
    )

    person_ids = fields.Many2many(
        'hr.employee',
        string=_('Personnes'),
    )

    # activities_description = fields.One2many(
    #     'digiit.ebios_rm.bv.description.activity',
    #     'business_value_id',
    #     string=_('Descriptions / Activités')
    # )

    classification = fields.Char(
        string=_('Classification'),
        help=_("""D,C,I,T\n
        D= Disponibilité,C= Confidentialité,I= Intégrité,T= Traçabilité\n
    1 - Impact faible : atteinte à l’actif a peu de conséquences sur l’organisation.
    2 - Impact modéré : atteinte à l’actif peut perturber le fonctionnement mais reste gérable.
    3 - Impact élevé : atteinte significative sur l’intégrité, la confidentialité ou la disponibilité.
    4 - Impact très élevé : atteinte majeure pouvant provoquer un arrêt ou un dommage grave à l’organisation.""")
    )

    # OLD: Actifs informationnels
    active_informational_ids = fields.One2many(
        'digiit.ebios_rm.actif.informationnel',
        'business_value_id',
        string=_('Actifs informationnel'),
        tracking=True,
    )

    # NEW: Bien support
    bien_support_ids = fields.One2many(
        'digiit.ebios_rm.bien.support',
        'business_value_id',
        string=_('Bien support'),
        tracking=True,
    )

    dreaded_event_ids = fields.One2many(
        'digiit.ebios_rm.dreaded.event',
        'business_value_id',
        string=_('Evénements redoutés'),
        tracking=True,
    )

    dreaded_event_count = fields.Integer(
        string=_('Nombre d\'événements redoutés'),
        compute='_compute_dreaded_event_count',
        store=True
    )

    strategic_scenario_count = fields.Integer(
        string=_('Nombre de scénarios stratégiques'),
        compute='_compute_strategic_scenario_count',
        store=True
    )

    @api.depends('dreaded_event_ids')
    def _compute_dreaded_event_count(self):
        for record in self:
            record.dreaded_event_count = len(record.dreaded_event_ids)

    @api.depends('dreaded_event_ids')
    def _compute_strategic_scenario_count(self):
        for record in self:
            record.strategic_scenario_count = self.env['digiit.ebios_rm.visual.strategic.scenario'].search_count([
                ('dreaded_event_id', 'in', record.dreaded_event_ids.ids)
            ])

    prefix = 'VM-'
    related_field = 'study_id'

    # @api.model
    # def create(self,vals):
    #     return super(BusinessValue,self).create(vals)
    # parent.business_value_ids += []

    # @api.model
    # def _get_employees(self):
    #     employees = self.env['hr.employee'].search([('company_id', '=', self.env.company.id)])
    #     return [(employee.id, employee.name) for employee in employees]

    # def generate_identifier(self, related_field_name ='', prefix='VM-', digits=2):

    #     print('---------',self.study_id.id)
    #     related_records = self.env['digiit.ebios_rm.study'].search([('id','=',self.study_id)]).business_value_ids

    #     next_number = 1
    #     if related_records:
    #         last_identifier = related_records[-1].identifier
    #         if last_identifier and last_identifier.startswith(prefix):
    #             next_number = int(last_identifier[len(prefix):]) + 1
    #     else: return ""
    #     return f"{prefix}{next_number:0{digits}d}"

    # @api.model
    # def _get_employee_selection(self):
    #     employees = self.env['hr.employee'].search([])
    #     return [(employee.id, employee.name) for employee in employees]

    def name_get(self):
        result = []
        for record in self:
            display_name = f"{record.identifier} {record.name}"
            result.append((record.display_name, display_name))
        return result

    # @api.model
    # def create(self, vals):
    #     record = super(BusinessValue, self).create(vals)
    #     record.study_id._auto_save() # Trigger autosave on create
    #     return record
    # @api.model
    # def write(self, vals):
    #     res = super(BusinessValue, self).write(vals)
    #     self.study_id._auto_save() # Trigger autosave on write
    #     return res

    @api.model_create_multi
    def create(self, vals):
        records = super().create(vals)

        for rec in records:
            rec.study_id.message_post(
                body=f"Valeur métier créée : {rec.name}",
            )

        return records

    def write(self, vals):
        res = super().write(vals)
        for rec in self:
            if rec.study_id:
                rec.study_id.message_post(body=f"Valeur métier modifiée : {rec.name}")
        return res


    def unlink(self):
        for rec in self:
            rec.study_id.message_post(
                body=f"Valeur métier supprimée : {rec.name}",
            )
        return super().unlink()
