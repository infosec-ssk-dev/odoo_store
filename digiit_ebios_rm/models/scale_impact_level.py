from odoo import models, fields,api,_

class ScaleImpactLevel(models.Model):
    _name = 'digiit.ebios_rm.scale.impact.level'
    _inherit=['digiit.ebios_rm.has.color']

    num = fields.Integer(
        string=_('Niveau'),
        tracking=True,
        required=True,
        )
    
    name = fields.Char(
        string=_('Nom'),
        compute='_compute_name',
        readonly=True
    )

    level_ids = fields.One2many(
        'digiit.ebios_rm.scale.impact.level.pivot',
        'scale_impact_level_id',
        string=_('échelles'),
        tracking=True,
        )
    
    @api.depends('num')
    def _compute_name(self):
        for record in self:
            if record.num:
                record.name = str(record.num)  
            else:
                record.name = False
    @api.model
    def create(self, vals): 
        record = super(ScaleImpactLevel, self).create(vals)
        impacts_records = self.env['digiit.ebios_rm.scale.impact'].search([])
        if impacts_records and len(impacts_records)>0:
            for impact in impacts_records:
                self.env['digiit.ebios_rm.scale.impact.level.pivot'].create({
                    'scale_impact_id': impact.id,
                    'scale_impact_level_id': record.id,
                })

    
	