from odoo import models, fields, api, _


class StudyReferential(models.Model):
    """Relation model between Study and Referential with additional fields"""
    _name = 'digiit.ebios_rm.study.referential'
    _description = 'Référentiel d\'étude avec statut'
    _rec_name = 'referential_id'

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_('Étude'),
        required=True,
        ondelete='cascade',
        index=True
    )

    referential_id = fields.Many2one(
        'digiit.ebios_rm.referential',
        string=_('Référentiel'),
        required=True,
        ondelete='cascade',
        index=True
    )

    # Referential info for easy display
    referential_version = fields.Char(
        string=_('Version'),
        related='referential_id.version',
        readonly=True
    )

    referential_chapter_count = fields.Integer(
        string=_('Nombre de chapitres'),
        related='referential_id.chapter_count',
        readonly=True
    )

    study_chapter_ids = fields.One2many(
        'digiit.ebios_rm.study.chapter',
        'study_referential_id',
        string=_('Chapitres de l\'étude')
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Create chapters automatically when study referential is created"""
        records = super().create(vals_list)
        for record in records:
            if record.referential_id and not record.study_chapter_ids:
                chapter_vals = []
                for chapter in record.referential_id.chapter_ids:
                    chapter_vals.append({
                        'study_id': record.study_id.id,
                        'study_referential_id': record.id,
                        'chapter_id': chapter.id,
                    })
                if chapter_vals:
                    self.env['digiit.ebios_rm.study.chapter'].create(chapter_vals)
        return records

    def action_view_chapters(self):
        """Open chapters for this referential"""
        self.ensure_one()
        return {
            'name': _('Chapitres - %s') % self.referential_id.name,
            'type': 'ir.actions.act_window',
            'res_model': 'digiit.ebios_rm.study.chapter',
            'view_mode': 'list,form',
            'domain': [('study_referential_id', '=', self.id)],
            'context': {
                'default_study_id': self.study_id.id,
                'default_study_referential_id': self.id,
            }
        }

    _sql_constraints = [
        ('unique_study_referential',
         'UNIQUE(study_id, referential_id)',
         'Ce référentiel est déjà ajouté à cette étude!')
    ]


class StudyChapter(models.Model):
    """Study-specific chapter data"""
    _name = 'digiit.ebios_rm.study.chapter'
    _description = 'Chapitre d\'étude avec statut'
    _rec_name = 'chapter_id'

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_('Étude'),
        required=True,
        ondelete='cascade',
        index=True
    )

    chapter_id = fields.Many2one(
        'digiit.ebios_rm.referential.chapter',
        string=_('Chapitre'),
        required=True,
        ondelete='cascade',
        index=True
    )

    study_referential_id = fields.Many2one(
        'digiit.ebios_rm.study.referential',
        string=_('Référentiel d\'étude'),
        required=True,
        ondelete='cascade',
        index=True
    )

    # Chapter info
    chapter_title = fields.Char(
        string=_('Titre'),
        related='chapter_id.chapter_title',
        readonly=True
    )

    chapter_number = fields.Char(
        string=_('Numéro'),
        related='chapter_id.chapter_id',
        readonly=True
    )

    chapter_description = fields.Text(
        string=_('Description'),
        related='chapter_id.description',
        readonly=True
    )

    study_measure_ids = fields.One2many(
        'digiit.ebios_rm.study.measure',
        'study_chapter_id',
        string=_('Mesures de l\'étude')
    )

    measure_count = fields.Integer(
        string=_('Nombre de mesures'),
        compute='_compute_measure_count',
        store=True
    )

    @api.depends('study_measure_ids')
    def _compute_measure_count(self):
        for record in self:
            record.measure_count = len(record.study_measure_ids)

    @api.model_create_multi
    def create(self, vals_list):
        """Create measures automatically when study chapter is created"""
        records = super().create(vals_list)
        for record in records:
            if record.chapter_id and not record.study_measure_ids:
                for measure in record.chapter_id.measure_ids:
                    self.env['digiit.ebios_rm.study.measure'].create({
                        'study_id': record.study_id.id,
                        'study_chapter_id': record.id,
                        'measure_id': measure.id,
                    })
        return records

    def action_view_measures(self):
        """Open measures for this chapter"""
        self.ensure_one()
        return {
            'name': _('Mesures - %s') % self.chapter_title,
            'type': 'ir.actions.act_window',
            'res_model': 'digiit.ebios_rm.study.measure',
            'view_mode': 'list,form',
            'domain': [('study_chapter_id', '=', self.id)],
            'context': {
                'default_study_id': self.study_id.id,
                'default_study_chapter_id': self.id,
            }
        }

    _sql_constraints = [
        ('unique_study_chapter',
         'UNIQUE(study_id, chapter_id)',
         'Ce chapitre est déjà ajouté à cette étude!')
    ]


class StudyMeasure(models.Model):
    """Study-specific measure data"""
    _name = 'digiit.ebios_rm.study.measure'
    _description = 'Mesure d\'étude avec statut'
    _rec_name = "display_name_custom"

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_('Étude'),
        required=True,
        ondelete='cascade',
        index=True
    )

    measure_id = fields.Many2one(
        'digiit.ebios_rm.referential.measure',
        string=_('Mesure'),
        required=True,
        ondelete='cascade',
        index=True
    )

    study_chapter_id = fields.Many2one(
        'digiit.ebios_rm.study.chapter',
        string=_('Chapitre d\'étude'),
        required=True,
        ondelete='cascade',
        index=True
    )

    # Measure info
    measure_title = fields.Char(
        string=_('Titre'),
        related='measure_id.measure_title',
        readonly=True
    )

    measure_number = fields.Char(
        string=_('Numéro'),
        related='measure_id.measure_id',
        readonly=True
    )

    measure_contenu = fields.Text(
        string=_('Contenu'),
        related='measure_id.contenu',
        readonly=True
    )

    measure_system_domains = fields.Many2many(
        'digiit.ebios_rm.system.domain',
        string=_('Domaines de sécurité'),
        related='measure_id.system_domain_ids',
        readonly=True
    )

    study_question_ids = fields.One2many(
        'digiit.ebios_rm.study.question',
        'study_measure_id',
        string=_('Questions de l\'étude')
    )

    question_count = fields.Integer(
        string=_('Nombre de questions'),
        compute='_compute_question_count',
        store=True
    )
    display_name_custom = fields.Char(
        string="Display Name",
        compute="_compute_display_name_custom",
        index=True,
    )

    @api.depends(
        "measure_title",
        "measure_number",
        "study_chapter_id.study_referential_id.referential_id.name",
        "study_chapter_id.study_referential_id.referential_id.version",
    )
    def _compute_display_name_custom(self):
        for r in self:
            ref = r.study_chapter_id.study_referential_id.referential_id
            if ref and r.measure_title:
                r.display_name_custom = f"{ref.name}:{ref.version} - {r.measure_number} - {r.measure_title}"
            else:
                r.display_name_custom = r.measure_title or f"Mesure {r.id}"

    def name_get(self):
        return [(r.id, r.display_name_custom or r.measure_title or f"Mesure {r.id}") for r in self]

    @api.depends('study_question_ids')
    def _compute_question_count(self):
        for record in self:
            record.question_count = len(record.study_question_ids)

    @api.model_create_multi
    def create(self, vals_list):
        """Create questions automatically when study measure is created"""
        records = super().create(vals_list)
        for record in records:
            if record.measure_id and not record.study_question_ids:
                for question in record.measure_id.question_ids:
                    self.env['digiit.ebios_rm.study.question'].create({
                        'study_id': record.study_id.id,
                        'study_measure_id': record.id,
                        'question_id': question.id,
                    })
        return records

    def action_view_questions(self):
        """Open questions for this measure"""
        self.ensure_one()
        return {
            'name': _('Questions - %s') % self.measure_title,
            'type': 'ir.actions.act_window',
            'res_model': 'digiit.ebios_rm.study.question',
            'view_mode': 'list,form',
            'domain': [('study_measure_id', '=', self.id)],
            'context': {
                'default_study_id': self.study_id.id,
                'default_study_measure_id': self.id,
            }
        }

    _sql_constraints = [
        ('unique_study_measure',
         'UNIQUE(study_id, measure_id)',
         'Cette mesure est déjà ajoutée à cette étude!')
    ]


class StudyQuestion(models.Model):
    """Study-specific question data with response"""
    _name = 'digiit.ebios_rm.study.question'
    _description = 'Question d\'étude avec réponse'
    _rec_name = 'question_id'

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_('Étude'),
        required=True,
        ondelete='cascade',
        index=True
    )

    question_id = fields.Many2one(
        'digiit.ebios_rm.referential.question',
        string=_('Question'),
        required=True,
        ondelete='cascade',
        index=True
    )

    study_measure_id = fields.Many2one(
        'digiit.ebios_rm.study.measure',
        string=_('Mesure d\'étude'),
        required=True,
        ondelete='cascade',
        index=True
    )

    # ADD HIERARCHY RELATED FIELDS
    study_chapter_id = fields.Many2one(
        'digiit.ebios_rm.study.chapter',
        string=_('Chapitre d\'étude'),
        related='study_measure_id.study_chapter_id',
        store=True,
        readonly=True
    )

    study_referential_id = fields.Many2one(
        'digiit.ebios_rm.study.referential',
        string=_('Référentiel d\'étude'),
        related='study_chapter_id.study_referential_id',
        store=True,
        readonly=True
    )

    # Question info
    question_text = fields.Char(
        string=_('Question'),
        related='question_id.question_text',
        readonly=True
    )

    question_number = fields.Char(
        string=_('Numéro'),
        related='question_id.question_id',
        readonly=True
    )

    # Reference fields from referential question
    plan_action = fields.Text(
        string=_('Plan d\'action'),
        related='question_id.plan_action',
        readonly=True
    )

    bonne_pratique = fields.Text(
        string=_('Bonne pratique'),
        related='question_id.bonne_pratique',
        readonly=True
    )

    vulnerabilite = fields.Text(
        string=_('Vulnérabilité'),
        related='question_id.vulnerabilite',
        readonly=True
    )

    response_application = fields.Selection(
        [
            ('oui', _("Oui")),
            ('non', _("Non")),
            ('non_applicable', _("Non applicable")),
        ],
        string=_("Réponse")
    )
    # Application status
    application_status_id = fields.Many2one(
        'digiit.ebios_rm.application.status.scale',
        string=_('État d\'application'),
        tracking=True
    )

    justification = fields.Text(
        string=_('Justification'),
        tracking=True
    )

    # Display helpers
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

    # System domains from measure
    system_domain_ids = fields.Many2many(
        'digiit.ebios_rm.system.domain',
        string=_('Domaines de sécurité'),
        related='study_measure_id.measure_system_domains',
        readonly=True
    )

    # Codes for display
    chapter_code = fields.Char(
        string=_('Code Chapitre'),
        related='study_chapter_id.chapter_number',
        store=True,
        readonly=True
    )

    measure_code = fields.Char(
        string=_('Code Mesure'),
        related='study_measure_id.measure_number',
        store=True,
        readonly=True
    )

    _sql_constraints = [
        ('unique_study_question',
         'UNIQUE(study_id, question_id)',
         'Cette question est déjà ajoutée à cette étude!')
    ]