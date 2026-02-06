from odoo import models, fields,api,_

class ThreatLevelLimit(models.Model):
    _name = 'digiit.ebios_rm.threat.level.limit'
    
    value = fields.Float(
            
        string=_('Valeur'),
        tracking=True,
        required=True,
        )
        
    is_minimal = fields.Boolean(
            
        string=_('Minimale?'),
        tracking=True,
        required=True,
        )
        
    is_maximal = fields.Boolean(
            
        string=_('Maximale?'),
        tracking=True,
        required=True,
        )
	
    held = fields.Boolean(
            
        string=_('Retenu?'),
        tracking=True,
        required=True,
        )
        
    name = fields.Char(
            
        string=_('Nom'),
        tracking=True,
        required=True,
        )
        
    color = fields.Char(
            
        string=_('Couleur'),
        tracking=True,
        required=True,
        )
    
    @api.onchange('is_maximal')
    def _onchange_max(self):
        if self.is_maximal:
            self.env['digiit.ebios_rm.threat.level.limit'].search([('id', '!=', self._origin.id), ('is_maximal', '=', True)]).write({'is_maximal': False})
            self.is_maximal = True  # Keep the selected one checked

    @api.onchange('is_minimal')
    def _onchange_min(self):
        if self.is_minimal:
            self.env['digiit.ebios_rm.threat.level.limit'].search([('id', '!=', self._origin.id), ('is_minimal', '=', True)]).write({'is_minimal': False})
            self.is_minimal = True

    @api.onchange('value')
    def _check_values(self):
        self.ensure_one()
        values = self.env['digiit.ebios_rm.threat.level.limit'].search([])
        min_record = self.search([('value', '!=', False)], order='value asc', limit=1)
        if(self.id == min_record.id):
            self.is_minimal = True

        max_record = self.search([('value', '!=', False)], order='value desc', limit=1)
        if(self.id == max_record.id):
            self.is_maximal = True
        