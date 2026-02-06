from odoo import models, fields, api, _
from odoo.exceptions import UserError


class Referential(models.Model):
    _name = 'digiit.ebios_rm.referential'
    _description = 'Référentiel de sécurité'
    _order = 'name'

    name = fields.Char(string=_('Nom'), required=True, tracking=True)
    version = fields.Char(string=_('Version'), required=True, tracking=True)
    description = fields.Text(string=_('Description'), tracking=True)
    chapter_ids = fields.One2many(
        'digiit.ebios_rm.referential.chapter',
        'referential_id',
        string=_('Chapitres')
    )
    chapter_count = fields.Integer(
        string=_('Nombre de chapitres'),
        compute='_compute_chapter_count'
    )

    # ADD THESE FIELDS FOR BULK IMPORT
    import_chapter_number = fields.Char(string='Import: Chapitre')
    import_chapter_title = fields.Char(string='Import: Titre Chapitre')
    import_measure_number = fields.Char(string='Import: Mesure')
    import_measure_title = fields.Char(string='Import: Titre Mesure')
    import_measure_content = fields.Text(string='Import: Contenu Mesure')
    import_question_text = fields.Char(string='Import: Question')
    import_system_domains = fields.Char(string='Import: Domaines de sécurité')

    @api.depends('chapter_ids')
    def _compute_chapter_count(self):
        for record in self:
            record.chapter_count = len(record.chapter_ids)

    @api.model
    def create(self, vals):
        """Override create to handle bulk import fields + éviter le doublon
        sur (name, version) lors des imports CSV/XLS.
        """

        # 1) Extraire les champs d'import (on les enlève de vals pour ne pas
        # essayer de les écrire directement sur le modèle principal)
        import_data = {
            'chapter_number': vals.pop('import_chapter_number', None),
            'chapter_title': vals.pop('import_chapter_title', None),
            'measure_number': vals.pop('import_measure_number', None),
            'measure_title': vals.pop('import_measure_title', None),
            'measure_content': vals.pop('import_measure_content', None),
            'question_text': vals.pop('import_question_text', None),
            'system_domains': vals.pop('import_system_domains', None),
        }

        # 2) Détecter si on est dans un import Odoo
        # (Odoo met généralement `import_file` dans le context)
        is_import = bool(self.env.context.get('import_file'))

        referential = None

        # 3) Si import et qu'on a déjà name + version dans les vals,
        #    essayer de récupérer un référentiel existant pour les mêmes valeurs
        if is_import and vals.get('name') and vals.get('version'):
            referential = self.search([
                ('name', '=', vals['name']),
                ('version', '=', vals['version']),
            ], limit=1)

        # 4) Si pas trouvé (ou pas en mode import), on crée normalement
        if not referential:
            referential = super().create(vals)
            # → ici, si (name, version) est en doublon, c'est la contrainte SQL
            #   qui lèvera l'erreur comme avant.

        # 5) Si des données d'import existent, on crée/complète les enfants
        if any(import_data.values()):
            self._create_from_import_data(referential, import_data)

        return referential

    def _create_from_import_data(self, referential, data):
        """Create chapter, measure, and question from import data"""

        # Skip if no question (main required field)
        if not data.get('question_text'):
            return

        # 1. Get or create Chapter
        chapter = self._get_or_create_chapter_for_import(
            referential,
            data.get('chapter_number'),
            data.get('chapter_title')
        )

        # 2. Get or create Measure
        measure = self._get_or_create_measure_for_import(
            chapter,
            data.get('measure_number'),
            data.get('measure_title'),
            data.get('measure_content'),
            data.get('system_domains')
        )

        # 3. Create Question
        self._create_question_for_import(
            measure,
            data.get('question_text')
        )

    def _get_or_create_chapter_for_import(self, referential, chapter_number, chapter_title):
        """Get or create chapter during import"""
        Chapter = self.env['digiit.ebios_rm.referential.chapter']

        if not chapter_number:
            # Auto-generate chapter number
            count = Chapter.search_count([('referential_id', '=', referential.id)])
            chapter_number = str(count + 1)

        if not chapter_title:
            chapter_title = f"Chapitre {chapter_number}"

        # Search existing
        chapter = Chapter.search([
            ('referential_id', '=', referential.id),
            ('chapter_id', '=', chapter_number)
        ], limit=1)

        if not chapter:
            chapter = Chapter.create({
                'referential_id': referential.id,
                'chapter_id': chapter_number,
                'chapter_title': chapter_title
            })

        return chapter

    def _get_or_create_measure_for_import(self, chapter, measure_number,
                                          measure_title, content, system_domains):
        """Get or create measure during import"""
        Measure = self.env['digiit.ebios_rm.referential.measure']

        if not measure_number:
            # Auto-generate measure number
            count = Measure.search_count([('chapter_id', '=', chapter.id)])
            measure_number = f"{chapter.chapter_id}.{count + 1}"

        if not measure_title:
            measure_title = f"Mesure {measure_number}"

        # Search existing
        measure = Measure.search([
            ('chapter_id', '=', chapter.id),
            ('measure_id', '=', measure_number)
        ], limit=1)

        if not measure:
            # Parse system domains
            domain_ids = self._parse_system_domains_for_import(system_domains)

            measure = Measure.create({
                'chapter_id': chapter.id,
                'measure_id': measure_number,
                'measure_title': measure_title,
                'contenu': content or '',
                'system_domain_ids': [(6, 0, domain_ids)]
            })

        return measure

    def _parse_system_domains_for_import(self, domains_str):
        """Parse system domains from comma-separated string"""
        if not domains_str:
            return []

        Domain = self.env['digiit.ebios_rm.system.domain']
        domain_ids = []

        # Split and clean
        domain_names = [d.strip().lstrip('#') for d in domains_str.split(',')]

        for domain_name in domain_names:
            if not domain_name:
                continue

            # Search by name or code
            domain = Domain.search([
                '|',
                ('name', 'ilike', domain_name.replace('_', ' ')),
                ('code', 'ilike', domain_name)
            ], limit=1)

            if not domain:
                # Create new domain
                code = domain_name.upper().replace(' ', '_')
                domain = Domain.create({
                    'name': domain_name.replace('_', ' '),
                    'code': code
                })

            domain_ids.append(domain.id)

        return domain_ids

    def _create_question_for_import(self, measure, question_text):
        """Create question during import"""
        if not question_text:
            return

        Question = self.env['digiit.ebios_rm.referential.question']

        # Check if question already exists
        existing = Question.search([
            ('measure_id', '=', measure.id),
            ('question_text', '=', question_text)
        ], limit=1)

        if existing:
            return  # Skip duplicate

        # Auto-generate question ID
        count = Question.search_count([('measure_id', '=', measure.id)])
        question_id = f"{measure.measure_id}.Q{count + 1}"

        Question.create({
            'measure_id': measure.id,
            'question_id': question_id,
            'question_text': question_text
        })

    _sql_constraints = [
        ('unique_name_version',
         'UNIQUE(name, version)',
         'Un référentiel avec ce nom et cette version existe déjà!')
    ]


class ReferentialChapter(models.Model):
    _name = 'digiit.ebios_rm.referential.chapter'
    _description = 'Chapitre de référentiel'
    _order = 'referential_id, chapter_id'
    _rec_name = 'chapter_title'

    chapter_title = fields.Char(string=_('Titre'), required=True)
    chapter_id = fields.Char(string=_('Chapitre ID'), required=True)
    description = fields.Text(string=_('Description'))
    referential_id = fields.Many2one(
        'digiit.ebios_rm.referential',
        string=_('Référentiel'),
        required=True,
        ondelete='cascade'
    )
    measure_ids = fields.One2many(
        'digiit.ebios_rm.referential.measure',
        'chapter_id',
        string=_('Mesures')
    )
    measure_count = fields.Integer(
        string=_('Nombre de mesures'),
        compute='_compute_measure_count'
    )

    @api.depends('measure_ids')
    def _compute_measure_count(self):
        for record in self:
            record.measure_count = len(record.measure_ids)

    def name_get(self):
        """Display chapter_id and title"""
        result = []
        for record in self:
            name = f"{record.chapter_id} - {record.chapter_title}"
            result.append((record.id, name))
        return result

    _sql_constraints = [
        ('unique_chapter_per_ref',
         'UNIQUE(referential_id, chapter_id)',
         'Ce chapitre existe déjà dans ce référentiel!')
    ]


class SystemDomain(models.Model):
    """System domains that can be assigned to measures"""
    _name = 'digiit.ebios_rm.system.domain'
    _description = 'Domaine système'
    _order = 'name'

    name = fields.Char(string=_('Nom'), required=True)
    code = fields.Char(string=_('Code'))
    description = fields.Text(string=_('Description'))
    measure_ids = fields.Many2many(
        'digiit.ebios_rm.referential.measure',
        'measure_domain_rel',
        'domain_id',
        'measure_id',
        string=_('Mesures')
    )

    _sql_constraints = [
        ('unique_code',
         'UNIQUE(code)',
         'Un domaine avec ce code existe déjà!')
    ]


class ReferentialMeasure(models.Model):
    _name = 'digiit.ebios_rm.referential.measure'
    _description = 'Mesure de référentiel'
    _order = 'chapter_id, measure_id'
    _rec_name = 'measure_title'

    measure_title = fields.Char(string=_('Titre'), required=True)
    measure_id = fields.Char(string=_('Mesure ID'), required=True)
    contenu = fields.Text(string=_('Contenu'))
    chapter_id = fields.Many2one(
        'digiit.ebios_rm.referential.chapter',
        string=_('Chapitre'),
        required=True,
        ondelete='cascade'
    )
    referential_id = fields.Many2one(
        'digiit.ebios_rm.referential',
        string=_('Référentiel'),
        related='chapter_id.referential_id',
        store=True,
        readonly=True
    )
    system_domain_ids = fields.Many2many(
        'digiit.ebios_rm.system.domain',
        'measure_domain_rel',
        'measure_id',
        'domain_id',
        string=_('Domaines de sécurité')
    )
    question_ids = fields.One2many(
        'digiit.ebios_rm.referential.question',
        'measure_id',
        string=_('Questions')
    )
    question_count = fields.Integer(
        string=_('Nombre de questions'),
        compute='_compute_question_count'
    )

    @api.depends('question_ids')
    def _compute_question_count(self):
        for record in self:
            record.question_count = len(record.question_ids)

    def name_get(self):
        """Display measure_id and title"""
        result = []
        for record in self:
            name = f"{record.measure_id} - {record.measure_title}"
            result.append((record.id, name))
        return result

    _sql_constraints = [
        ('unique_measure_per_chapter',
         'UNIQUE(chapter_id, measure_id)',
         'Cette mesure existe déjà dans ce chapitre!')
    ]


class ReferentialQuestion(models.Model):
    _name = 'digiit.ebios_rm.referential.question'
    _description = 'Question de mesure'
    _order = 'measure_id, question_id'
    _rec_name = 'question_text'

    question_id = fields.Char(string=_('Question ID'), required=True)
    question_text = fields.Char(string=_('Question Text'), required=True)
    plan_action = fields.Text(string=_('Plan d\'action'))
    bonne_pratique = fields.Text(string=_('Bonne pratique'))
    vulnerabilite = fields.Text(string=_('Vulnérabilité'))

    # Champs d'import pour l'inverse (questions -> ref/chapitre/mesure)
    import_ref_name = fields.Char(string="Import: Référentiel")
    import_ref_version = fields.Char(string="Import: Version Référentiel")
    import_chapter_number = fields.Char(string="Import: Chapitre")
    import_chapter_title = fields.Char(string="Import: Titre Chapitre")
    import_measure_number = fields.Char(string="Import: Mesure")
    import_measure_title = fields.Char(string="Import: Titre Mesure")
    import_measure_content = fields.Text(string="Import: Contenu Mesure")
    import_system_domains = fields.Char(string="Import: Domaines de sécurité")

    measure_id = fields.Many2one(
        'digiit.ebios_rm.referential.measure',
        string=_('Mesure'),
        required=True,
        ondelete='cascade'
    )
    chapter_id = fields.Many2one(
        'digiit.ebios_rm.referential.chapter',
        string=_('Chapitre'),
        related='measure_id.chapter_id',
        store=True,
        readonly=True
    )
    referential_id = fields.Many2one(
        'digiit.ebios_rm.referential',
        string=_('Référentiel'),
        related='measure_id.referential_id',
        store=True,
        readonly=True
    )
    system_domain_ids = fields.Many2many(
        'digiit.ebios_rm.system.domain',
        string=_('Domaines de sécurité'),
        related='measure_id.system_domain_ids',
        readonly=True
    )
    chapter_code = fields.Char(
        string=_('Code Chapitre'),
        related='chapter_id.chapter_id',
        store=True,
        readonly=True
    )
    measure_code = fields.Char(
        string=_('Code Mesure'),
        related='measure_id.measure_id',
        store=True,
        readonly=True
    )

    @api.model_create_multi
    def create(self, vals_list):
        Referential = self.env['digiit.ebios_rm.referential']
        Chapter = self.env['digiit.ebios_rm.referential.chapter']
        Measure = self.env['digiit.ebios_rm.referential.measure']
        ReferModel = self.env['digiit.ebios_rm.referential']

        is_import = bool(self.env.context.get('import_file'))

        for vals in vals_list:
            # 0) En import, on ne fait pas confiance à un measure_id mappé depuis le CSV
            if is_import:
                vals.pop('measure_id', None)
                vals.pop('chapter_id', None)
                vals.pop('referential_id', None)

            # 1) Extraire les champs d'import
            ref_name = vals.pop('import_ref_name', None)
            ref_version = vals.pop('import_ref_version', None)
            chap_num = vals.pop('import_chapter_number', None)
            chap_title = vals.pop('import_chapter_title', None)
            meas_num = vals.pop('import_measure_number', None)
            meas_title = vals.pop('import_measure_title', None)
            meas_content = vals.pop('import_measure_content', None)
            system_domains = vals.pop('import_system_domains', None)

            measure = None

            # 2) Si on a ref + version, on reconstruit toute la hiérarchie
            if ref_name and ref_version:
                referential = Referential.search([
                    ('name', '=', ref_name),
                    ('version', '=', ref_version),
                ], limit=1)
                if not referential:
                    referential = Referential.create({
                        'name': ref_name,
                        'version': ref_version,
                    })

                # Chapitre
                if not chap_num:
                    existing_chapter_count = Chapter.search_count([
                        ('referential_id', '=', referential.id)
                    ])
                    chap_num = str(existing_chapter_count + 1)
                if not chap_title:
                    chap_title = f"Chapitre {chap_num}"

                chapter = Chapter.search([
                    ('referential_id', '=', referential.id),
                    ('chapter_id', '=', chap_num),
                ], limit=1)
                if not chapter:
                    chapter = Chapter.create({
                        'referential_id': referential.id,
                        'chapter_id': chap_num,
                        'chapter_title': chap_title,
                    })

                # Mesure
                if not meas_num:
                    existing_measure_count = Measure.search_count([
                        ('chapter_id', '=', chapter.id)
                    ])
                    meas_num = f"{chapter.chapter_id}.{existing_measure_count + 1}"
                if not meas_title:
                    meas_title = f"Mesure {meas_num}"

                measure = Measure.search([
                    ('chapter_id', '=', chapter.id),
                    ('measure_id', '=', meas_num),
                ], limit=1)
                if not measure:
                    domain_ids = ReferModel._parse_system_domains_for_import(
                        system_domains
                    )
                    measure = Measure.create({
                        'chapter_id': chapter.id,
                        'measure_id': meas_num,
                        'measure_title': meas_title,
                        'contenu': meas_content or '',
                        'system_domain_ids': [(6, 0, domain_ids)],
                    })

            # 3) Si toujours pas de mesure mais on a un question_id de type "8.11.Q1"
            if not measure and vals.get('question_id'):
                qid = vals['question_id']
                # on essaye de prendre la partie avant ".Q"
                # ex: "8.11.Q1" -> "8.11"
                if '.Q' in qid:
                    prefix = qid.split('.Q')[0]  # "8.11"
                    # On cherche une mesure avec ce code
                    measure = Measure.search([
                        ('measure_id', '=', prefix),
                    ], limit=1)

            # 4) Si on a trouvé ou créé une mesure, on l'assigne
            if measure:
                vals['measure_id'] = measure.id

            # 5) Si toujours aucune mesure -> on lève une erreur claire
            if not vals.get('measure_id'):
                raise UserError(_(
                    "Impossible de déterminer la mesure pour la question:\n"
                    "- ID: %s\n- Texte: %s\n\n"
                    "Vérifiez que votre fichier d'import fournit soit:\n"
                    "- les colonnes de référentiel/chapitre/mesure (Import: ...),\n"
                    "  soit une mesure déjà existante correspondant à l'ID de question (ex: 8.11 pour 8.11.Q1)."
                ) % (
                    vals.get('question_id', '/'),
                    vals.get('question_text', '/')
                ))

            # 6) Génération du question_id si vide (fallback)
            qid = (vals.get('question_id') or '').strip()
            if not qid:
                # IDs parlants par mesure
                count_for_measure = self.search_count([
                    ('measure_id', '=', vals['measure_id'])
                ])
                m = Measure.browse(vals['measure_id'])
                code = m.measure_id or 'M'
                vals['question_id'] = f"{code}.Q{count_for_measure + 1}"

        return super().create(vals_list)