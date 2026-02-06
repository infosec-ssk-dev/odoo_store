from odoo import models, api

class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model
    def write(self, vals):
        res = super(ResUsers, self).write(vals)
        print("res users vals: ",vals)
        # action_vals = {
        #     'name': 'Etudes Ebios RM',
        #     'res_model': 'digiit_ebios_rm.study',
        #     'view_mode': 'list,form',
        #     'domain': f"[('company_id', '=', user.company_id.id)]",
        #     'context': "{'default_company_id': user.company_id.id}",
        # }
        # # Check if company_id was changed
        # if 'company_id' in vals:
        #     # Check if the action already exists
        #     action = self.env['ir.actions.act_window'].search([('name', '=', 'digiit_ebios_study_action')])
        #     if action:
        #         # Update the existing action
        #         action.write(action_vals)
        #     else:
        #         # Create a new action
        #         action = self.env['ir.actions.act_window'].create(action_vals)
        
        return res