# -*- coding: utf-8 -*-
"""Members-only authentication for every DIGI-ERP AI endpoint.

Hiding a menu is not access control: without this, ANY logged-in user could
still POST to /ai-solution/ask and talk to the assistant, because the routes
only asked for `auth='user'`. This registers a custom Odoo auth method so a
route can declare `auth='ai_user'` and be rejected at the framework level —
before the controller body runs — for anyone outside the DIGI-ERP AI groups.

Werkzeug's Forbidden is raised on purpose: `ir.http._authenticate_explicit`
re-raises HTTPException untouched but converts anything else into AccessDenied,
which would bounce a perfectly-valid session to the login screen. A 403 tells
the user the truth — they are logged in, they just have no AI access.
"""
import werkzeug.exceptions

from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _auth_method_ai_user(cls):
        """Logged in AND a member of DIGI-ERP AI / User (or Administrator)."""
        cls._auth_method_user()
        if not request.env.user.has_group(
                "digi_erp_ai_solution.group_ai_user"):
            raise werkzeug.exceptions.Forbidden(
                "You do not have access to the AI assistant. "
                "Ask your administrator to grant you the DIGI-ERP AI access "
                "level in Settings → Users."
            )

    @classmethod
    def _auth_method_ai_manager(cls):
        """Reserved for endpoints that expose configuration (API keys, …)."""
        cls._auth_method_user()
        if not request.env.user.has_group(
                "digi_erp_ai_solution.group_ai_manager"):
            raise werkzeug.exceptions.Forbidden(
                "This action is reserved to DIGI-ERP AI administrators."
            )
