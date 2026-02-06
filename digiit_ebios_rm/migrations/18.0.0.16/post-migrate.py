from odoo import api, SUPERUSER_ID

def migrate(cr, version):
    """
    Migration script to:
    1. Create missing matrix records for existing Motivation/Ressource combinations
    2. Auto-assign pertinence values to existing matrix records
    """
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Use sudo() for all search operations to bypass record rules
    motivations = env['digiit.ebios_rm.motivation'].sudo().search([])
    ressources = env['digiit.ebios_rm.ressource'].sudo().search([])

    print(f"Found {len(motivations)} motivations and {len(ressources)} ressources")

    # Create missing matrix records with sudo()
    existing_matrix = env['digiit.ebios_rm.pertinence.matrix'].sudo().search([])
    existing_combinations = set()
    for matrix in existing_matrix:
        existing_combinations.add((matrix.motivation_id.id, matrix.ressource_id.id))

    matrix_vals_list = []
    for motivation in motivations:
        for ressource in ressources:
            if (motivation.id, ressource.id) not in existing_combinations:
                matrix_vals_list.append({
                    'motivation_id': motivation.id,
                    'ressource_id': ressource.id,
                })

    if matrix_vals_list:
        print(f"Creating {len(matrix_vals_list)} missing matrix records")
        env['digiit.ebios_rm.pertinence.matrix'].sudo().create(matrix_vals_list)

    # Update all matrix records to have correct pertinence with sudo()
    all_matrix_records = env['digiit.ebios_rm.pertinence.matrix'].sudo().search([])
    print(f"Updating pertinence for {len(all_matrix_records)} matrix records")

    # Use sudo() to bypass record rules for the method calls
    for matrix_record in all_matrix_records:
        matrix_record.sudo()._auto_set_pertinence()

    # Recompute the concerned_partner_id for all records with sudo()
    business_value_model = env['digiit.ebios_rm.business.value']
    business_value_records = business_value_model.sudo().search([])
    business_value_records.sudo()._compute_concerned_partner_bv()

    risk_treatment_model = env['digiit.ebios_rm.risk.treatment']
    risk_treatment_records = risk_treatment_model.sudo().search([])
    risk_treatment_records.sudo()._compute_concerned_partner_rt()


    print("Migration completed successfully")
