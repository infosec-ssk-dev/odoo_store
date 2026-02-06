from odoo import models, fields,api,_
from odoo.tools.translate import _
from odoo.exceptions import ValidationError
import unicodedata

class ScaleImpact(models.Model):
    _name = 'digiit.ebios_rm.scale.impact'
    
    name = fields.Char(
        string=_('Nom'),
        tracking=True,
        required=True,
        )
    level_ids = fields.One2many(
        'digiit.ebios_rm.scale.impact.level.pivot',
        'scale_impact_id',
        string=_('Niveaux'),
        tracking=True,
        required=True,
        )
    code= fields.Char(
        string=_('code'),
        compute='_compute_code', store=True
        )
	
    description = fields.Html(   
        string=_('Description'),
        tracking=True,
        )
    
    def normalize(s):
        return ''.join(c for c in unicodedata.normalize('NFD', s).replace(" ", "_").lower()
                  if unicodedata.category(c) != 'Mn')
	
    @api.depends('name')
    def _compute_code(self):
        for record in self:
            if record.name:
                record.code = record.normalize(record.name)

            else:
                record.code = False 

    @api.constrains('code')
    def _check_unique_code(self):
        for record in self:
            if record.code:
                existing_record = self.search([('code', '=', record.code), ('id', '!=', record.id)])
                if existing_record:
                    raise ValidationError(_("Le code \'%s\' est déjà utilisé.") % record.code)
                
    # @api.model
    # def create(self, vals): 
    #     record = super(ScaleImpact, self).create(vals)
    #     level_records = self.env['digiit.ebios_rm.scale.impact.level'].search([])
    #     if level_records and len(level_records)>0:
    #         for level in level_records:
    #             self.env['digiit.ebios_rm.scale.impact.level.pivot'].create({
    #                 'scale_impact_id': record.id,
    #                 'scale_impact_level_id': level.id,
    #             })


	