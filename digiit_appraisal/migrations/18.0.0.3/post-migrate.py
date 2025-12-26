from odoo import api, SUPERUSER_ID
import logging

_logger = logging.getLogger(__name__)

def migrate(cr, version):
    """ Run on module upgrade - Set all existing templates to global and clean up singleton settings """
    if not version:
        return  # safety check

    env = api.Environment(cr, SUPERUSER_ID, {})
    
    try:
        # Set all existing templates to be global (company_id = False)
        templates = env['digiit.model.appraisal_template'].search([])
        if templates:
            templates.write({'company_id': False})
            _logger.info(f"Updated {len(templates)} appraisal templates to be global (company_id = False)")
        
        # Delete existing singleton settings records (they will be recreated per-company as needed)
        settings = env['digiit.model.appraisal.settings'].search([])
        if settings:
            settings.unlink()
            _logger.info(f"Deleted {len(settings)} existing singleton settings records")
        
        _logger.info("Migration completed: Set all existing templates to global and cleaned up singleton settings")
        
    except Exception as e:
        _logger.error(f"Error during migration: {str(e)}")
        raise
