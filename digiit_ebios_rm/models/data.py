from odoo import api, SUPERUSER_ID

def create_or_update_study_action(cr, registry):
    env = api.Environment(cr, SUPERUSER_ID, {})
    
    # Define the action values
    action_vals = {
        'name': 'Etudes Ebios RM',
        'res_model': 'study.study',
        'view_mode': 'tree,form',
        'domain': "[('company_id', '=', user.company_id.id)]",
        'context': "{'default_company_id': user.company_id.id}",
    }
    
    # Check if the action already exists
    action = env['ir.actions.act_window'].search([('name', '=', 'digiit_ebios_study_action')])
    if action:
        # Update the existing action
        action.write(action_vals)
    else:
        # Create a new action
        action = env['ir.actions.act_window'].create(action_vals)
    
    # Return the action ID for reference
    return action.id