import base64
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class DigiitDeclarationSuspension(models.Model):
    _name = 'digiit.declaration.suspension'
    _description = 'Déclaration Factures en Suspension sur CA'
    _order = 'date desc'

    name = fields.Char(string='Nom')
    trimestre = fields.Selection(
        selection=[
            ('1', '1er Trimestre'),
            ('2', '2ème Trimestre'),
            ('3', '3ème Trimestre'),
            ('4', '4ème Trimestre'),
        ],
        string='Trimestre',
    )
    annee_fiscale = fields.Char(string='Année Fiscale', size=4)

    @api.constrains('annee_fiscale')
    def _check_annee_fiscale(self):
        for rec in self:
            if rec.annee_fiscale and (not rec.annee_fiscale.isdigit() or len(rec.annee_fiscale) != 4):
                raise ValidationError(_('L\'année fiscale doit être une année à 4 chiffres.'))

    @api.onchange('trimestre', 'annee_fiscale', 'company_id')
    def _onchange_generate_name(self):
        trimestre_labels = {
            '1': 'T1',
            '2': 'T2',
            '3': 'T3',
            '4': 'T4',
        }
        trimestre_label = trimestre_labels.get(self.trimestre, '')
        annee = self.annee_fiscale or ''
        company = self.company_id.name or ''
        if trimestre_label and annee and company:
            self.name = _('Déclaration Suspension TVA %s %s %s') % (trimestre_label, annee, company)

    date = fields.Date(string='Date', required=True)
    company_id = fields.Many2one(
        'res.company', string='Société',
        default=lambda self: self.env.company,
        required=True,
    )
    fichier_declaration = fields.Binary(string='Fichier Déclaration', readonly=True)
    fichier_declaration_filename = fields.Char(string='Nom du fichier')

    line_ids = fields.One2many(
        'digiit.declaration.suspension.line', 'declaration_id', string='Lignes'
    )

    def action_calculer(self):
        self.ensure_one()

        # Determine date range for trimestre
        trimestre_map = {
            '1': (1, 3),
            '2': (4, 6),
            '3': (7, 9),
            '4': (10, 12),
        }
        month_start, month_end = trimestre_map[self.trimestre]

        from datetime import date
        import calendar
        fiscal_year_number = int(self.annee_fiscale)

        date_from = date(fiscal_year_number, month_start, 1)
        last_day = calendar.monthrange(fiscal_year_number, month_end)[1]
        date_to = date(fiscal_year_number, month_end, last_day)

        # Find bon commande visé records in this period with autorisation filled and invoice posted
        bons = self.env['digiit.bon.commande.vise'].search([
            ('digiit_autorisation_id', '!=', False),
            ('move_id.state', '=', 'posted'),
            ('date_facture', '>=', date_from),
            ('date_facture', '<=', date_to),
        ])

        if not bons:
            raise UserError(_('Aucun bon de commande visé trouvé pour ce trimestre.'))

        # Clear existing lines
        self.line_ids.unlink()

        lines = []
        for idx, bon in enumerate(bons, start=1):
            partner = bon.partner_id
            autorisation = bon.digiit_autorisation_id
            lines.append({
                'declaration_id': self.id,
                'num_ordre': idx,
                'num_facture': bon.numero_facture,
                'date_facture': bon.date_facture,
                'type_identifiant': 'MF',  # default, user can edit
                'identifiant': partner.vat or '',
                'nom_prenom_raison': partner.name or '',
                'adresse': ' '.join(filter(None, [
                    partner.street, partner.city, partner.country_id.name
                ])),
                'num_autorisation': autorisation.num_autorisation if autorisation else '',
                'date_autorisation': autorisation.date_debut if autorisation else False,
                'prix_ht': bon.montant_ht,
                # taux/montant fields left at 0 for manual entry
            })

        self.env['digiit.declaration.suspension.line'].create(lines)

        missing = [b.partner_id.name for b in bons if not b.partner_id.vat]
        if missing:
            raise UserError(
                _('Calcul effectué mais les partenaires suivants n\'ont pas de Matricule Fiscale :\n%s')
                % '\n'.join(missing)
            )

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def action_generer_fichier(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_('Veuillez d\'abord calculer les lignes.'))

        company = self.company_id
        vat = company.vat or ''
        year = self.annee_fiscale or ''
        trimestre = 'T' + (self.trimestre or '')

        def fmt_amount(value, digits):
            return str(int(round(value * 1000))).zfill(digits)

        def fmt_taux(value):
            return str(int(round(value))).zfill(3)

        def fmt_date(d):
            return d.strftime('%d%m%Y') if d else ''

        def fmt_order(n):
            return str(n).zfill(6)

        lines_txt = []

        # E line — header
        company_address = ' '.join(filter(None, [
            company.street or '',
            company.city or '',
            company.country_id.name if company.country_id else '',
        ]))
        lines_txt.append('EF%s%s%s\t%s\t%s' % (
            vat, year, trimestre,
            company.name or '',
            company_address,
        ))

        # D lines — one per declaration line
        for line in self.line_ids:
            prefix = 'DF%s%s%s%s' % (vat, year, trimestre, fmt_order(line.num_ordre))
            # after address: remaining fields are concatenated (no tabs)
            concatenated = ''.join([
                line.num_autorisation or '',
                fmt_date(line.date_autorisation),
                fmt_amount(line.prix_ht, 15),
                fmt_taux(line.taux_fod),
                fmt_amount(line.montant_fod, 17),
                fmt_taux(line.taux_cons),
                fmt_amount(line.montant_cons, 17),
                fmt_taux(line.taux_tva),
                fmt_amount(line.montant_tva, 17),
            ])
            lines_txt.append('\t'.join([
                prefix,
                fmt_date(line.date_facture),
                fmt_order(line.num_ordre),
                line.identifiant or '',
                line.nom_prenom_raison or '',
                line.adresse or '',
                concatenated,
            ]))

        # T line — totals
        total_lines = len(self.line_ids)
        total_prix_ht = sum(self.line_ids.mapped('prix_ht'))
        total_montant_fod = sum(self.line_ids.mapped('montant_fod'))
        total_montant_cons = sum(self.line_ids.mapped('montant_cons'))
        total_montant_tva = sum(self.line_ids.mapped('montant_tva'))

        t_prefix = 'TF%s%s%s%s' % (vat, year, trimestre, fmt_order(total_lines))
        # totals concatenated, preceded by empty tabs matching D line structure
        totals_concatenated = ''.join([
            fmt_amount(total_prix_ht, 15),
            fmt_amount(total_montant_fod, 17),
            fmt_amount(total_montant_cons, 17),
            fmt_amount(total_montant_tva, 17),
        ])
        lines_txt.append('\t'.join([
            t_prefix,
            '', '', '', '',     # empty: date, numorder, identifiant, partnername
            totals_concatenated,
        ]))

        content = '\n'.join(lines_txt)
        filename = _('declaration_suspension_%s_%s.txt') % (year, trimestre)
        self.write({
            'fichier_declaration': base64.b64encode(content.encode('utf-8')),
            'fichier_declaration_filename': filename,
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }


class DigiitDeclarationSuspensionLine(models.Model):
    _name = 'digiit.declaration.suspension.line'
    _description = 'Ligne Déclaration Suspension TVA'
    _order = 'num_ordre asc'

    declaration_id = fields.Many2one(
        'digiit.declaration.suspension', string='Déclaration',
        required=True, ondelete='cascade'
    )
    num_ordre = fields.Integer(string='N° Ordre')
    num_facture = fields.Char(string='Numéro Facture')
    date_facture = fields.Date(string='Date Facture')
    type_identifiant = fields.Char(string='Type Identifiant')
    identifiant = fields.Char(string='Identifiant')
    nom_prenom_raison = fields.Char(string='Nom et Prénom / Raison Sociale')
    adresse = fields.Char(string='Adresse')
    num_autorisation = fields.Char(string='Numéro Autorisation')
    date_autorisation = fields.Date(string='Date Autorisation')
    prix_ht = fields.Float(string='Prix (Hors Taxes)', digits=(16, 3))
    montant_fod = fields.Float(string='Montant FOD', digits=(16, 3))
    taux_fod = fields.Float(string='Taux FOD (%)', digits=(5, 2))
    taux_cons = fields.Float(string='Taux Cons (%)', digits=(5, 2))
    montant_cons = fields.Float(string='Montant Cons', digits=(16, 3))
    taux_tva = fields.Float(string='Taux TVA (%)', digits=(5, 2))
    montant_tva = fields.Float(string='Montant TVA', digits=(16, 3))