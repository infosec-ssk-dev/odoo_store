# -*- coding: utf-8 -*-
{
    'name': 'DIGI-ERP AI',
    'version': '18.0.1.0.0',
    'summary': 'DIGI-ERP AI — AI assistant integrated into Odoo',
    'description': """
DIGI-ERP AI — an AI assistant that works inside your Odoo
=========================================================

Ask questions about your own data in plain language, and let the assistant do
the work: look records up, count and summarise them, create or update them,
build reports, and read the documents your team uploads.

Key points
----------

* **Your permissions apply.** The assistant works through the connected user's
  own Odoo access rights, record rules and field permissions. It can never show
  or change something that user could not reach on their own.
* **Two access levels.** *User* chats and imports documents; *Administrator*
  configures the AI models and holds the API keys. Chats stay private — an
  administrator does not read other people's conversations.
* **Any AI, including none of the cloud ones.** Host a model yourself so nothing
  leaves your network, or connect OpenAI, Anthropic Claude, Google Gemini,
  DeepSeek, xAI Grok, or any OpenAI-compatible API.
* **Files and reports.** Exports and analytical documents in PDF, Excel, Word
  and PowerPoint, generated from the real records rather than retyped.
* **In context.** A sidebar assistant follows the user across the backend and
  understands the screen they are on.
* **Reads what you attach.** Drop a PDF, image or Office file into the chat and
  ask about it, or have it translated or rewritten with its layout intact.

Turning uploaded invoices, bills and orders into Odoo records is a separate
add-on, **DIGI-ERP AI — Documents**, which installs on top of this one.
""",
    'category': 'Productivity',
    'author': 'DIGI-ERP',
    'depends': ['portal', 'web', 'mail'],
    # Needed to read a file a user attaches to the chat, and by the
    # `modify_document` tool that rewrites one while keeping its layout.
    # NOTE: python-pptx is intentionally NOT gated here — the .pptx branch
    # degrades gracefully with an "install python-pptx" message, so a prod
    # without it still loads the module. Install it via requirements.txt.
    # Legacy .ppt/.doc conversion needs the `soffice`/LibreOffice binary on
    # PATH; .7z/.rar are optional (py7zr / rarfile) and degrade gracefully.
    'external_dependencies': {
        # PyPI distribution names (NOT import names): the 'docx' import comes
        # from the 'python-docx' distribution.
        #
        # numpy is the one HARD requirement: services/schema_rag.py and
        # services/orm_kb.py import it at module level and both are loaded
        # through services/__init__.py at install time, so without it the
        # module does not merely lose a feature — it fails to import at all.
        # It is NOT part of Odoo's own requirements.txt, so it has to be
        # declared here or a plain server gets a raw ImportError traceback
        # instead of Odoo's clean "Unmet dependency" message.
        'python': ['numpy', 'python-docx', 'openpyxl', 'pypdf'],
    },
    'data': [
        # Groups FIRST: the ACL csv and the record rules both reference them.
        'security/ai_groups.xml',
        'security/ir.model.access.csv',
        'security/conversation_rules.xml',
        'views/ai_model_views.xml',
        'views/conversation_views.xml',
        'views/res_config_settings_views.xml',
        'views/portal_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'digi_erp_ai_solution/static/src/css/chat.css',
            'digi_erp_ai_solution/static/src/css/dai_charts.css',
            # Inline canvas charts — plain IIFE, must load before chat.js
            'digi_erp_ai_solution/static/src/js/dai_charts.js',
            'digi_erp_ai_solution/static/src/js/chat.js',
        ],
        'web.assets_backend': [
            'digi_erp_ai_solution/static/src/css/provider_kanban.css',
            # Embedded context-aware chat sidebar (systray)
            'digi_erp_ai_solution/static/src/css/embedded_chat.css',
            'digi_erp_ai_solution/static/src/css/dai_charts.css',
            # Inline canvas charts — plain IIFE, must load before embedded_chat.js
            'digi_erp_ai_solution/static/src/js/dai_charts.js',
            'digi_erp_ai_solution/static/src/js/embedded_chat.js',
            'digi_erp_ai_solution/static/src/xml/embedded_chat.xml',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3'
}
