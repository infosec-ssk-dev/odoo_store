{
    "name": "Elfatoora TTN - DigiGo",
    "version": "19.0.1.0",
    "category": "Accounting/Localization",
    "summary": "DigiGo signing provider for Elfatoora TTN",
    "description": """
        Extends **Elfatoora TTN - Base** with the DigiGo mobile/cloud signature flow
        (OAuth2 authorize URL, token exchange, and signing API integration).

        Install **digiit_elfatoora_ttn_base** first, then this module to enable
        DigiGo as a signing method in Elfatoora configuration and wizards.
    """,
    "author": "Digi-ERP",
    "website": "https://digi-it.com.tn",
    "license": "LGPL-3",
    "depends": ["digiit_elfatoora_ttn_base"],
    "data": [
        "security/ir.model.access.csv",
        "security/ir_rule.xml",
        "views/elfatoora_config_digigo_views.xml",
        "views/elfatoora_submission_wizard_digigo_views.xml",
    ],
    "images": ["static/description/cover.png"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
