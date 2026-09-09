# -*- coding: utf-8 -*-
{
    'name': 'DIGI-ERP AI',
    'version': '19.0.1.0.0',
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
    'support': 'helpdesk@digi-it.com.tn',
    'depends': ['portal', 'web', 'mail'],
    # NOTHING is declared in `external_dependencies` ON PURPOSE.
    #
    # Every library below is imported lazily, inside a try/except, and its
    # absence produces a plain "install X to read this file type" message on
    # the one upload that needed it. Declaring them would instead make Odoo
    # REFUSE TO INSTALL the whole module — turning an optional file format
    # into a hard blocker, and making the app impossible to install on Odoo
    # Online, where nothing can be pip-installed.
    #
    # Two of them were previously declared and were outright wrong:
    #   • pypdf     — the PDF branch already falls back to PyPDF2, and PyPDF2
    #                 is what Odoo itself ships below Python 3.13. Requiring
    #                 `pypdf` blocked installs that would have worked.
    #   • python-docx — not in Odoo's requirements.txt at all, so a stock
    #                 Odoo could never install this module.
    #
    # Optional, each unlocking one file type:
    #   python-docx (.docx)   openpyxl (.xlsx, ships with Odoo)
    #   pypdf / PyPDF2 (.pdf) python-pptx (.pptx)   xlrd (.xls)
    #   py7zr (.7z)           rarfile (.rar)
    # Legacy .doc/.ppt conversion needs the `soffice` (LibreOffice) binary.
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
    # Listing gallery on the Odoo Store. The first entry is the image shown
    # on the app card, so it leads with the thing people are buying: the chat.
    'images': [
        # The FIRST entry is what the store uses as the cover picture at the
        # top of the listing. It has to be a designed banner, not a
        # screenshot, or the page opens on a wall of Odoo UI.
        'static/description/banner.png',
        'static/description/chat.png',
        'static/description/sidebar.png',
        'static/description/models.png',
        'static/description/model-form.png',
        'static/description/settings.png',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3'
}
