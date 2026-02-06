from odoo import models, fields, api, _


class RiskTreatment(models.Model):
    _name = 'digiit.ebios_rm.risk.treatment'
    _rec_name = 'identifier'


    risk_id = fields.Many2one(
        'digiit.ebios_rm.operational.scenario',
        string=_('Risque'),
        tracking=True,
        ondelete="cascade",
        required=True,
    )

    identifier = fields.Char(

        string=_('ID'),
        related='risk_id.prefixed_identifier',
        tracking=True,
    )

    attack_path = fields.Html(

        string=_('Description du chemin d\'attaque'),
        related='risk_id.attack_path',
    )

    attack_path_text = fields.Text(
        string=_('Description du chemin d\'attaque (texte)'),
        related='risk_id.attack_path_text',
        store=False
    )

    severity = fields.Selection(
        string=_('Gravité'),
        related='risk_id.seriousness',
        help=_("""
        1 - Mineure: Aucun impact opérationnel ni sur les performances de l'activité ni sur la sécurité des personnes et/ou des biens. L'organisation surmontera la situation sans trop de difficultés)____________________
        2 - Moyenne: Dégradation des performances de l'activité sans impact sur la sécurité des personnes et/ou des biens. L'organisation surmontera la situation malgré quelques difficultés)____________________
        3 - Forte: Forte dégradation des performances de l'activité, avec d'éventuels impacts significatifs sur la sécurité des personnes et/ou des biens. L'organisation surmontera la situation avec de sérieuses difficultés (fonctionnement en mode dégradé)____________________
        4 - Critique: Incapacité pour l'organisation d'assurer la totalité ou une partie de son activité, avec d'éventuels impacts graves sur la sécurité des personnes et/ou des biens. L'organisation ne surmontera vraisemblablement pas la situation (sa survie est menacée).""")
    )

    likelihood = fields.Selection(
        string=_('Vraisemblance'),
        related='risk_id.likelihood',
        help=_("""
    1 - Peu probable: La source de risque a peu de chance d'atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est faible.____________________
    2 - Probable: La source de risque est susceptible d'atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est significative.____________________
    3 - Très probable: La source de risque va probablement atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est élevée.____________________
    4 - Quasi certain: La source de risque va certainement atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est très élevée.""")
    )

    risk_level = fields.Char(
        string=_('Niveau de risque'),
        compute='_calculate_risk_level',
        tracking=True,
        help=_("""1 - Faible: Risque acceptable en l'état. Aucune action n'est à entreprendre.____________________
    2 - Modéré: Risque acceptable en l'état sous réserve d'une surveillance régulière.____________________
    3 - Élevé: Risque inacceptable. Un suivi en termes de gestion du risque est à mener et des actions sont à mettre en place.____________________
    4 - Critique: Risque inacceptable. Des mesures de réduction du risque doivent impérativement être prises à court terme (3 mois).""")
    )

    treatment_strategy = fields.Selection(
        [
            ('1', _('Modéré')),
            ('2', _('Accepter')),
            ('3', _('Évitement')),
            ('4', _('Partage')),
        ],
        string=_('Options de traitement (6.1.3.a)'),
        tracking=True,
    )

    treatment_measures = fields.Text(

        string=_('M.T.R.G (Palliatives et confinement)'),
        help=_('Mesures de traitement des risques sur la gravité (Palliatives et confinement)'),
        tracking=True,
    )

    risidual_severity = fields.Selection(
        [
            ('1', _('1')),
            ('2', _('2')),
            ('3', _('3')),
            ('4', _('4')),
        ],
        string=_('Gravité résiduelle'),
        tracking=True,
        help=_("""
    1 - Mineure: Aucun impact opérationnel ni sur les performances de l'activité ni sur la sécurité des personnes et/ou des biens. L'organisation surmontera la situation sans trop de difficultés)____________________
    2 - Moyenne: Dégradation des performances de l'activité sans impact sur la sécurité des personnes et/ou des biens. L'organisation surmontera la situation malgré quelques difficultés)____________________
    3 - Forte: Forte dégradation des performances de l'activité, avec d'éventuels impacts significatifs sur la sécurité des personnes et/ou des biens. L'organisation surmontera la situation avec de sérieuses difficultés (fonctionnement en mode dégradé)____________________
    4 - Critique: Incapacité pour l'organisation d'assurer la totalité ou une partie de son activité, avec d'éventuels impacts graves sur la sécurité des personnes et/ou des biens. L'organisation ne surmontera vraisemblablement pas la situation (sa survie est menacée).""")
    )

    risidual_likelihood = fields.Selection(
        [
            ('1', _('1')),
            ('2', _('2')),
            ('3', _('3')),
            ('4', _('4')),
        ],
        string=_('Niveau de risque résiduel'),
        tracking=True,
        help=_("""
    1 - Peu probable: La source de risque a peu de chance d'atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est faible.____________________
    2 - Probable: La source de risque est susceptible d'atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est significative.____________________
    3 - Très probable: La source de risque va probablement atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est élevée.____________________
    4 - Quasi certain: La source de risque va certainement atteindre son objectif visé selon l'un des modes opératoires envisagés. La vraisemblance du scénario est très élevée.""")
    )
    risidual_risk_level = fields.Char(
        string=_(''),
        compute='_calculate_risidual_risk_level',
        tracking=True,
        help=_("""1 - Faible: Risque acceptable en l'état. Aucune action n'est à entreprendre.____________________
    2 - Modéré: Risque acceptable en l'état sous réserve d'une surveillance régulière.____________________
    3 - Élevé: Risque inacceptable. Un suivi en termes de gestion du risque est à mener et des actions sont à mettre en place.____________________
    4 - Critique: Risque inacceptable. Des mesures de réduction du risque doivent impérativement être prises à court terme (3 mois).""")
    )

    risidual_treatment_strategy = fields.Selection(
        [
            ('1', _('Modéré')),
            ('2', _('Accepter')),
            ('3', _('Évitement')),
            ('4', _('Partage')),
        ],
        string=_('Stratégie de traitement'),
        tracking=True,
    )

    risk_owner = fields.Many2one(
        'res.partner',
        string=_('Propriétaire de risque'),
        tracking=True,
        default=lambda self: self.business_value.responsible_entity_person if self.business_value else False,
        domain="[('parent_id', '=', concerned_partner_id)]"
    )

    concerned_partner_id = fields.Many2one(
        'res.partner',
        string='Partenaire concerné',
        compute='_compute_concerned_partner_rt'
    )

    @api.depends('study_id', 'study_id.is_for_partner', 'study_id.partner', 'study_id.company_id')
    def _compute_concerned_partner_rt(self):
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

    owner_validation_date = fields.Date(

        string=_('Date de validation de propriétaire'),
        tracking=True,
    )

    justification = fields.Char(

        string=_('Justification'),
        tracking=True,
    )

    dreaded_event = fields.Many2one(
        'digiit.ebios_rm.dreaded.event',
        string=_('Evénement redouté'),
        related='risk_id.dreaded_event_id',
        tracking=True,
    )

    business_value = fields.Many2one(
        'digiit.ebios_rm.business.value',
        string=_('Valeur métier'),
        tracking=True,
        related='risk_id.dreaded_event_id.business_value_id',
    )

    observations = fields.Html(

        string=_('Observations'),
    )

    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        ondelete='cascade',
    )

    removed = fields.Boolean(string=_('Supprimé?'), default=False)

    probability_treatment_mesures = fields.Text(

        string=_('M.T.R.P (Préventives, Dissuasives)'),
        help=_('Mesures de traitement des risques sur la probabilité (Préventives, Dissuasives)  (6.1.3.b)'),
        tracking=True,
    )
    equivalent_mesures_referentiel = fields.Many2many(
        'digiit.ebios_rm.study.measure',
        'risk_treatment_study_measure_rel',
        'risk_treatment_id',
        'study_measure_id',
        string=_('Mesures équivalentes ou nécessaires provenant des référentiels de l\'étude'),
        domain="[('study_id', '=', study_id)]",
        tracking=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Only if risk_owner not provided explicitly
            if not vals.get('risk_owner') and vals.get('risk_id'):
                risk = self.env['digiit.ebios_rm.operational.scenario'].browse(vals['risk_id'])
                bv = risk.dreaded_event_id.business_value_id
                if bv and bv.responsible_entity_person:
                    vals['risk_owner'] = bv.responsible_entity_person.id

        records = super().create(vals_list)

        for rec in records:
            if rec.study_id:
                rec.study_id.message_post(
                    body=f"Traitement de risque créé : {rec.identifier}"
                )

        return records

    def write(self, vals):
        res = super().write(vals)

        for rec in self:
            if rec.study_id:
                rec.study_id.message_post(
                    body=f"Traitement de risque modifié : {rec.identifier}"
                )

        return res

    def unlink(self):
        deletion_info = []
        for rec in self:
            if rec.study_id:
                deletion_info.append((rec.study_id, rec.identifier))

        res = super().unlink()

        for study, identifier in deletion_info:
            study.message_post(
                body=f"Traitement de risque supprimé : {identifier}"
            )

        return res

    def _calculate_risk_level_value(self, severity_value, liklyhood_value):
        severity = severity_value if severity_value else ""  # Handle cases where risk is not set
        likelihood = liklyhood_value if liklyhood_value else ""  # Handle cases where target is not set
        result = self.env['digiit.ebios_rm.risk.level.matrix'].search(
            [('gravity', '=', severity), ('likelihood', '=', likelihood)])
        if len(result) > 0:
            return result[0].risk_level
        return '0'

    @api.depends('severity', 'likelihood')
    def _calculate_risk_level(self):
        for record in self:
            record.risk_level = record._calculate_risk_level_value(record.severity, record.likelihood)

    @api.depends('risidual_severity', 'risidual_likelihood', 'severity', 'likelihood')
    def _calculate_risidual_risk_level(self):
        for record in self:
            if record.risidual_severity and record.risidual_likelihood:
                record.risidual_risk_level = record._calculate_risk_level_value(
                    record.risidual_severity, record.risidual_likelihood
                )
            else:
                # If not filled, default to risk_level (same as initial)
                record.risidual_risk_level = record._calculate_risk_level_value(
                    record.severity, record.likelihood
                )
