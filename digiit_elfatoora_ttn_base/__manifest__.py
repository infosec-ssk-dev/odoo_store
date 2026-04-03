{
    "name": "Elfatoora TTN - Base",
    "version": "19.0.1.0",
    "category": "Accounting/Localization",
    "summary": "Shared core for Tunisian Elfatoora (TEIF) and TTN e-invoicing",
    "description": """
        Base module for the Tunisian Elfatoora (TTN) e-invoicing integration.

        This addon provides:
        - TEIF XML generation (v1.8.8)
        - XAdES signature preparation, embedding, and verification
        - Submission tracking, invoice fields, and status checks
        - Signing wizard framework (extensible by provider modules)
    """,
    "author": "Digi-ERP",
    "website": "https://digi-it.com.tn",
    "license": "LGPL-3",
    "depends": ["base", "account", "contacts", "barcodes"],
    "data": [
        "security/ir.model.access.csv",
        "security/ir_rule.xml",
        "views/elfatoora_config_views.xml",
        "views/elfatoora_submission_wizard_views.xml",
        "views/elfatoora_submission_views.xml",
        "views/signature_check_wizard_views.xml",
        "views/standalone_signature_checker_views.xml",
        "views/account_move_views.xml",
        "views/elfatoora_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "digiit_elfatoora_ttn_base/static/src/js/signing_method_field.js",
            "digiit_elfatoora_ttn_base/static/src/xml/signing_method_field.xml",
        ],
    },
    "images": ["static/description/cover.png"],
    "installable": True,
    "application": False,
    "auto_install": False,
    "uninstall_hook": "uninstall_hook",
}
