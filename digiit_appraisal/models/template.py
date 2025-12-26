from odoo import models, fields, api

# Template Model
class AppraisalTemplate(models.Model):
    _name = "digiit.model.appraisal_template"
    _description = "Appraisal Template"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(required=True, tracking=True)
    employee_html = fields.Html("Employee Section")
    manager_html = fields.Html("Manager Section")
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        domain="[('id', 'in', allowed_company_ids)]"
    )
