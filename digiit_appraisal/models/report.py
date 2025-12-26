from odoo import models, fields, tools


class AppraisalAnalysis(models.Model):
    _name = "digiit.model.appraisal.analysis"
    _description = "Analyse des évaluations"
    _auto = False
    _rec_name = 'employee_id'

    employee_id = fields.Many2one('hr.employee', string='Employé', readonly=True)
    manager_id = fields.Many2one('hr.employee', string='Manager', readonly=True)
    department_id = fields.Many2one('hr.department', string='Département', readonly=True)
    job_title = fields.Char(string='Poste', readonly=True)
    company_id = fields.Many2one('res.company', string='Société', readonly=True)
    status = fields.Selection([
        ('draft', 'Brouillon'),
        ('confirmed', 'Confirmé'),
        ('done', 'Terminé'),
        ('canceled', 'Annulé'),
    ], string='Statut', readonly=True)
    date_evaluation = fields.Date(string="Date limite", readonly=True)
    create_date = fields.Datetime(string='Date de création', readonly=True)
    template_id = fields.Many2one('digiit.model.appraisal_template', string="Modèle d'évaluation", readonly=True)
    note_finale_id = fields.Many2one('digiit.model.appraisal_grid', string="Note finale", readonly=True)
    employee_feedback_done = fields.Boolean(string="Auto-évaluation terminée", readonly=True)
    manager_feedback_done = fields.Boolean(string="Évaluation manager terminée", readonly=True)

    # Computed fields for analysis
    is_late = fields.Boolean(string='En retard', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                SELECT 
                    a.id,
                    a.employee_id,
                    a.manager_id,
                    e.department_id,
                    a.job_title,
                    a.company_id,
                    a.status,
                    a.date_evaluation,
                    a.create_date,
                    a.template_id,
                    a.note_finale_id,
                    a.employee_feedback_done,
                    a.manager_feedback_done,
                    CASE 
                        WHEN a.status != 'done' AND a.date_evaluation < CURRENT_DATE 
                        THEN TRUE 
                        ELSE FALSE 
                    END as is_late
                FROM digiit_model_appraisal a
                LEFT JOIN hr_employee e ON a.employee_id = e.id
            )
        """ % self._table)


class SkillEvolutionReport(models.Model):
    _name = "digiit.model.skill.evolution.report"
    _description = "Rapport d'évaluation des compétences"
    _auto = False
    _rec_name = 'employee_id'

    employee_id = fields.Many2one('hr.employee', string='Employé', readonly=True)
    type_id = fields.Many2one('digiit.model.skill_type', string='Type de compétences', readonly=True)
    tag_id = fields.Many2one('digiit.model.skill_tag', string='Compétence', readonly=True)
    level_id = fields.Many2one('digiit.model.skill_level', string='Niveau actuel', readonly=True)
    progression = fields.Float(string='Progrès actuels (%)', readonly=True, aggregator='avg')
    previous_progression = fields.Float(string='Progrès précédent (%)', readonly=True, aggregator='avg')
    progression_evolution = fields.Float(string='Évolution des progrès', readonly=True, aggregator='avg')
    previous_level_id = fields.Many2one('digiit.model.skill_level', string='Niveau précédent', readonly=True)
    justification = fields.Text(string='Justificatif', readonly=True)
    date_evaluation = fields.Date(string="Date d'évaluation", readonly=True)
    previous_date_evaluation = fields.Date(string="Précédente date d'évaluation", readonly=True)
    appraisal_id = fields.Many2one('digiit.model.appraisal', string='Évaluation', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                WITH current_skills AS (
                    SELECT 
                        s.id,
                        s.appraisal_id,
                        a.employee_id,
                        a.date_evaluation,
                        s.type_id,
                        s.tag_id,
                        s.level_id,
                        s.progression,
                        s.justification,
                        ROW_NUMBER() OVER (
                            PARTITION BY a.employee_id, s.type_id, s.tag_id 
                            ORDER BY a.date_evaluation DESC
                        ) as rn_current
                    FROM digiit_model_appraisal_skill s
                    INNER JOIN digiit_model_appraisal a ON s.appraisal_id = a.id
                    WHERE a.status = 'done'
                ),
                previous_skills AS (
                    SELECT 
                        s.appraisal_id,
                        a.employee_id,
                        a.date_evaluation as previous_date_evaluation,
                        s.type_id,
                        s.tag_id,
                        s.level_id as previous_level_id,
                        s.progression as previous_progression,
                        ROW_NUMBER() OVER (
                            PARTITION BY a.employee_id, s.type_id, s.tag_id 
                            ORDER BY a.date_evaluation DESC
                        ) as rn_previous
                    FROM digiit_model_appraisal_skill s
                    INNER JOIN digiit_model_appraisal a ON s.appraisal_id = a.id
                    WHERE a.status = 'done'
                )
                SELECT 
                    c.id,
                    c.employee_id,
                    c.type_id,
                    c.tag_id,
                    c.level_id,
                    c.progression / 100.0 as progression,
                    p.previous_level_id,
                    p.previous_progression / 100.0 as previous_progression,
                    c.justification,
                    c.date_evaluation,
                    p.previous_date_evaluation,
                    c.appraisal_id,
                    COALESCE((c.progression - p.previous_progression) / 100.0, 0) as progression_evolution,
                    CASE 
                        WHEN c.progression > COALESCE(p.previous_progression, 0) THEN TRUE
                        ELSE FALSE
                    END as has_improved
                FROM current_skills c
                LEFT JOIN previous_skills p ON (
                    c.employee_id = p.employee_id 
                    AND c.type_id = p.type_id 
                    AND c.tag_id = p.tag_id
                    AND p.rn_previous = 2
                    AND p.previous_date_evaluation < c.date_evaluation
                )
                WHERE c.rn_current = 1
            )
        """ % self._table)
