from odoo import models, fields, _
from odoo.exceptions import UserError
import base64
import logging

from ..services.digital_signature import DigitalSignature

_logger = logging.getLogger(__name__)


class StandaloneSignatureChecker(models.TransientModel):
    _name = "digiit_elfatoora.standalone_signature_checker"
    _description = "Standalone XML Signature Checker"

    xml_file = fields.Binary(string="XML File", required=True, help="Upload an XML file to check its digital signature(s)")
    xml_filename = fields.Char(string="Filename")
    result_text = fields.Html(string="Verification Result", readonly=True, sanitize=False)

    def action_check_signature(self):
        self.ensure_one()
        if not self.xml_file:
            raise UserError(_("Please upload an XML file first."))

        try:
            xml_bytes = base64.b64decode(self.xml_file)
            signer = DigitalSignature()
            summary = signer.verify_xml_signature(xml_bytes)
            self.result_text = summary
            return {
                "type": "ir.actions.act_window",
                "name": _("XML Signature Checker"),
                "res_model": self._name,
                "res_id": self.id,
                "view_mode": "form",
                "target": "new",
                "views": [(False, "form")],
            }
        except UserError as e:
            error_message = str(e) if e.args else _("Unknown error occurred")
            self.result_text = _("Signature verification failed:\n%s") % error_message
            return {
                "type": "ir.actions.act_window",
                "name": _("XML Signature Checker"),
                "res_model": self._name,
                "res_id": self.id,
                "view_mode": "form",
                "target": "new",
                "views": [(False, "form")],
            }
        except Exception as e:
            _logger.error("Unexpected error during signature check: %s", e, exc_info=True)
            error_msg = _("An unexpected error occurred during signature verification:\n%s") % str(e)
            self.result_text = error_msg
            raise UserError(error_msg)

