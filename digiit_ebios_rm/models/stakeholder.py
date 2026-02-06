from odoo import models, fields, api, _


class EbiosStakeholder(models.Model):
    _name = 'digiit.ebios_rm.stakeholder'
    _description = 'EBIOS RM Stakeholder'
    _rec_name = 'name'

    name = fields.Char(
        string='Nom',
        required=True,
        tracking=True
    )
    
    category = fields.Selection(
        [
            ('personnels', _('Personnels')),
            ('customers', _('Clients')),
            ('partenaires', _('Partenaires')),
            ('prestataires', _('Prestataires')),
        ],
        string=_('Catégorie'),
        default="personnels",
        tracking=True,
    )
    
    is_internal = fields.Boolean(
        string='Interne?',
        default=False,
        tracking=True
    )
    
    icon = fields.Image(
        string="Icon"
    )
    
    description = fields.Html(
        string="Description"
    )
    
    # Optional fields - add if you need them from the original PIP model
    power = fields.Integer(
        string="Pouvoir",
    )
    
    interest = fields.Integer(
        string="Intérêt",
    )
    
    pertinence = fields.Integer(
        string="Pertinence",
        compute='_compute_pertinence',
        store=True,
    )
    
    # Relation fields
    business_value_ids = fields.Many2many(
        'digiit.ebios_rm.business.value',
        relation='business_value_stakeholder_rel',
        column1='stakeholder_id',
        column2='bv_id',
        string='Business Values'
    )
    
    ecosystem_ids = fields.One2many(
        'digiit.ebios_rm.ecosystem',
        'stakeholder_id',
        string='Ecosystems'
    )
    
    @api.depends('power', 'interest')
    def _compute_pertinence(self):
        """Simple pertinence calculation - customize as needed"""
        for record in self:
            if record.power and record.interest:
                record.pertinence = record.power * record.interest
            else:
                record.pertinence = 0
    
    _sql_constraints = [
        ('name_unique', 'UNIQUE(name)', 'A stakeholder with this name already exists!')
    ]