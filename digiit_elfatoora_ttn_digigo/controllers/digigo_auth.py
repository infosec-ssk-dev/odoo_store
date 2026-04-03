from odoo import http
from odoo.http import request
import logging

_logger = logging.getLogger(__name__)


class DigiGoAuthController(http.Controller):
    @http.route("/digigo/fallback", type="http", auth="public", website=True)
    def digigo_callback(self, **kwargs):
        code = kwargs.get("code")
        token = kwargs.get("token")

        if not code and not token:
            _logger.error("DigiGo Callback Error: No code/token received")
            return request.make_response("Error: No authorization code received.", headers={"Content-Type": "text/plain"})

        if not code and token:
            _logger.info("Received 'token' parameter, treating as code or SAD.")
            code = token

        invoice_id = request.session.get("digigo_invoice_id")
        if invoice_id:
            request.session.pop("digigo_invoice_id", None)
        else:
            _logger.error("No Invoice ID found in session for DigiGo callback.")

        if invoice_id:
            invoice = request.env["account.move"].sudo().browse(invoice_id)
            if not invoice.exists():
                return request.make_response("Error: Invoice not found", headers={"Content-Type": "text/plain"})

            try:
                if token:
                    invoice._process_digigo_callback(sad=token)
                else:
                    invoice._process_digigo_callback(code=code)

                redirect_url = "/web#id=%s&model=account.move&view_type=form" % invoice_id
                return request.redirect(redirect_url)
            except Exception as e:
                _logger.error("DigiGo signing failed for invoice %s: %s", invoice_id, e)
                return request.make_response(f"Error: Signing failed: {str(e)}", headers={"Content-Type": "text/plain"})

        return request.make_response("Error: Invalid invoice reference", headers={"Content-Type": "text/plain"})

