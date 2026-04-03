from odoo import models, fields, _
from odoo.exceptions import UserError
import base64
import logging
from lxml import etree

from ..services.elfatoora_api import ElfatooraAPI
from ..services.teif_generator import TEIFGenerator
from ..services.digital_signature import DigitalSignature

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    digiit_elfatoora_status = fields.Selection(
        [
            ("draft", "Unsigned"),
            ("signed", "Signed"),
            ("ack", "Received by TTN"),
            ("pending", "Pending"),
            ("validated", "Validated"),
            ("rejected", "Rejected"),
        ],
        string="Elfatoora Status",
        default="draft",
        copy=False,
        readonly=True,
        tracking=True,
        help="Status of the invoice in the Elfatoora system",
    )

    digiit_elfatoora_ttn_reference = fields.Char(string="TTN Reference", copy=False, readonly=True, help="The final official fiscal ReferenceTTN")
    digiit_elfatoora_tracking_number = fields.Char(string="Tracking Number", copy=False, readonly=True, help="The initial receipt number (Numéro de Dépôt) from TTN")

    digiit_elfatoora_processing_date = fields.Datetime(string="Processing Date", copy=False, readonly=True, help="Date of last status update from TTN")
    digiit_elfatoora_validation_date = fields.Datetime(string="Validation Date", copy=False, readonly=True, help="Date when the invoice became legally valid")

    digiit_elfatoora_error_message = fields.Text(string="Error Message", copy=False, readonly=True)

    digiit_elfatoora_submission_id = fields.Many2one("digiit_model_elfatoora.submission", string="Last Submission", copy=False)

    digiit_elfatoora_signed_xml = fields.Binary(string="Signed XML", copy=False, attachment=True)
    digiit_elfatoora_signed_xml_filename = fields.Char(string="Signed XML Filename")

    digiit_elfatoora_ttn_xml = fields.Binary(
        string="TTN Returned XML",
        copy=False,
        attachment=True,
        help="Complete XML returned by TTN including both signatures and RefTtnVal",
    )
    digiit_elfatoora_ttn_xml_filename = fields.Char(string="TTN XML Filename")

    digiit_elfatoora_qr_code = fields.Binary(string="QR Code", copy=False, attachment=True)

    digiit_elfatoora_submission_count = fields.Integer(compute="_compute_elfatoora_submission_count", string="Submissions")

    pending_signing_hash = fields.Char(string="Pending Signing Hash", copy=False)
    pending_signing_xml = fields.Text(string="Pending Signing XML", copy=False)
    pending_signing_credential_id = fields.Char(string="Pending Signing Credential ID", copy=False)

    def _get_qr_code_data_uri(self):
        self.ensure_one()
        if not self.digiit_elfatoora_qr_code:
            return False
        if isinstance(self.digiit_elfatoora_qr_code, bytes):
            return "data:image/png;base64," + self.digiit_elfatoora_qr_code.decode("utf-8")
        return "data:image/png;base64," + str(self.digiit_elfatoora_qr_code)

    def _compute_elfatoora_submission_count(self):
        for move in self:
            move.digiit_elfatoora_submission_count = self.env["digiit_model_elfatoora.submission"].search_count([("invoice_id", "=", move.id)])

    def _extract_qr_code_from_xml(self, xml_content):
        try:
            if isinstance(xml_content, str):
                xml_content = xml_content.encode("utf-8")
            root = etree.fromstring(xml_content)
            ref_ttn_val = root.find(".//RefTtnVal")
            if ref_ttn_val is not None:
                reference_cev = ref_ttn_val.find("ReferenceCEV")
                if reference_cev is not None and reference_cev.text:
                    return reference_cev.text.encode("utf-8")
        except Exception as e:
            _logger.warning("Failed to extract QR code from XML: %s", e)
        return False

    def _get_qr_code_for_invoice(self, config, ttn_ref):
        api_client = ElfatooraAPI(config, invoice=self)
        validated_xml = api_client.download_validated_xml(ttn_ref)
        if not validated_xml:
            _logger.error("Could not download validated XML for invoice %s", self.name)
            return False

        qr_code = self._extract_qr_code_from_xml(validated_xml)
        if qr_code:
            return qr_code
        _logger.error("QR code not found in validated XML for invoice %s", self.name)
        return False

    def _save_signed_submission(self, signed_xml, config, extra_response=None):
        filename = f"{self.name}_signed.xml".replace("/", "_")

        self.write(
            {
                "digiit_elfatoora_signed_xml": base64.b64encode(signed_xml),
                "digiit_elfatoora_signed_xml_filename": filename,
                "digiit_elfatoora_status": "signed",
                "digiit_elfatoora_error_message": False,
                "digiit_elfatoora_tracking_number": False,
                "digiit_elfatoora_ttn_reference": False,
                "digiit_elfatoora_processing_date": False,
                "digiit_elfatoora_validation_date": False,
                "digiit_elfatoora_qr_code": False,
            }
        )

        return True

    def action_submit_signed_xml(self):
        """Submit the already signed XML to TTN."""
        self.ensure_one()
        if not self.digiit_elfatoora_signed_xml:
            raise UserError(_("No signed XML found. Please sign the invoice first."))

        config = self.env["digiit_model_elfatoora.config"].search([("company_id", "=", self.company_id.id)], limit=1)
        if not config:
            raise UserError(_("Elfatoora configuration not found."))

        try:
            signed_xml = base64.b64decode(self.digiit_elfatoora_signed_xml)
            api_client = ElfatooraAPI(config, invoice=self)
            response = api_client.submit_invoice(signed_xml)

            status = response.get("status", "ack")
            tracking_number = response.get("tracking_number") or response.get("ttn_ref") or False
            ttn_ref = response.get("ttn_ref") if status == "validated" else False

            proc_date = response.get("processing_date") or False

            update_vals = {
                "digiit_elfatoora_status": status,
                "digiit_elfatoora_tracking_number": tracking_number,
                "digiit_elfatoora_ttn_reference": ttn_ref,
                "digiit_elfatoora_processing_date": proc_date,
                "digiit_elfatoora_validation_date": proc_date if status == "validated" else False,
                "digiit_elfatoora_error_message": response.get("message") if status == "rejected" else False,
            }

            if status == "validated" and ttn_ref:
                update_vals["digiit_elfatoora_qr_code"] = self._get_qr_code_for_invoice(config, ttn_ref)

            self.write(update_vals)

            submission = self.env["digiit_model_elfatoora.submission"].create(
                {
                    "invoice_id": self.id,
                    "status": status,
                    "submission_method": "webservice",
                    "request_xml": signed_xml.decode("utf-8") if isinstance(signed_xml, bytes) else signed_xml,
                    "response_xml": str(response),
                    "tracking_number": tracking_number,
                    "ttn_reference": ttn_ref,
                    "processing_date": proc_date,
                    "error_message": response.get("message") if status == "rejected" else False,
                }
            )
            self.digiit_elfatoora_submission_id = submission.id
            return True
        except Exception as e:
            self.digiit_elfatoora_error_message = str(e)
            raise UserError(str(e))

    def action_submit_to_elfatoora(self):
        """Open the signing wizard."""
        self.ensure_one()
        return self._validate_for_elfatoora()

    def _validate_for_elfatoora(self):
        """Validate invoice + config readiness for Elfatoora signing."""
        self.ensure_one()
        if self.state != "posted":
            raise UserError(_("Invoice must be posted."))

        config = self.env["digiit_model_elfatoora.config"].search([("company_id", "=", self.company_id.id)], limit=1)
        if not config:
            raise UserError(_("Elfatoora configuration not found for this company."))

        if not (config.digigo_enabled_effective or config.enterprise_enabled_effective or config.token_enabled_effective):
            raise UserError(
                _(
                    "No signing method is available. "
                    "Please enable at least one method in Elfatoora Configuration."
                )
            )

        return {
            "name": _("Sign Invoice"),
            "type": "ir.actions.act_window",
            "res_model": "digiit_model_elfatoora.submission_wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_invoice_id": self.id},
        }

    def action_check_elfatoora_status(self):
        """Check status from TTN and store TTN-returned XML/QR when present."""
        self.ensure_one()

        config = self.env["digiit_model_elfatoora.config"].search([("company_id", "=", self.company_id.id)], limit=1)
        if not config:
            raise UserError(_("Elfatoora configuration not found."))

        try:
            api_client = ElfatooraAPI(config, invoice=self)
            response = api_client.check_status(self.digiit_elfatoora_tracking_number)

            status = response.get("status")
            vals = {
                "digiit_elfatoora_status": status,
                "digiit_elfatoora_tracking_number": response.get("tracking_number") or self.digiit_elfatoora_tracking_number,
                "digiit_elfatoora_processing_date": response.get("processing_date") if response else False,
                "digiit_elfatoora_validation_date": False,
            }

            xml_content = response.get("xml_content")
            xml_bytes = None
            if xml_content:
                xml_bytes = xml_content.encode("utf-8") if isinstance(xml_content, str) else xml_content
                try:
                    parser = etree.XMLParser(remove_blank_text=True)
                    root_clean = etree.fromstring(xml_bytes, parser)
                    xml_bytes_clean = etree.tostring(root_clean, pretty_print=False, encoding="UTF-8", xml_declaration=True)
                    if len(xml_bytes_clean) < len(xml_bytes):
                        xml_bytes = xml_bytes_clean
                except Exception as e:
                    _logger.warning("Failed to canonicalize TTN XML: %s", e)

                filename = f"{self.name}_ttn.xml".replace("/", "_")
                vals.update(
                    {
                        "digiit_elfatoora_ttn_xml": base64.b64encode(xml_bytes),
                        "digiit_elfatoora_ttn_xml_filename": filename,
                    }
                )

            if status == "validated":
                vals.update(
                    {
                        "digiit_elfatoora_ttn_reference": response.get("ttn_ref"),
                        "digiit_elfatoora_validation_date": response.get("v_date") if response else False,
                        "digiit_elfatoora_error_message": False,
                    }
                )
                if xml_bytes:
                    qr_code = self._extract_qr_code_from_xml(xml_bytes)
                    if qr_code:
                        vals["digiit_elfatoora_qr_code"] = qr_code
            else:
                vals["digiit_elfatoora_qr_code"] = False
                if status == "rejected":
                    vals.update({"digiit_elfatoora_error_message": response.get("message") or _("Rejected by TTN.")})

            self.write(vals)

            latest_submission = self.env["digiit_model_elfatoora.submission"].search([("invoice_id", "=", self.id)], order="create_date desc", limit=1)
            if latest_submission:
                latest_submission.write(
                    {
                        "status": self.digiit_elfatoora_status,
                        "error_message": self.digiit_elfatoora_error_message,
                        "ttn_reference": self.digiit_elfatoora_ttn_reference,
                        "tracking_number": self.digiit_elfatoora_tracking_number,
                        "processing_date": self.digiit_elfatoora_processing_date,
                        "validation_date": self.digiit_elfatoora_validation_date,
                        "response_xml": str(response),
                    }
                )
                self.digiit_elfatoora_submission_id = latest_submission.id
            else:
                submission = self.env["digiit_model_elfatoora.submission"].create(
                    {
                        "invoice_id": self.id,
                        "status": self.digiit_elfatoora_status,
                        "error_message": self.digiit_elfatoora_error_message,
                        "ttn_reference": self.digiit_elfatoora_ttn_reference,
                        "tracking_number": self.digiit_elfatoora_tracking_number,
                        "processing_date": self.digiit_elfatoora_processing_date,
                        "validation_date": self.digiit_elfatoora_validation_date,
                        "response_xml": str(response),
                    }
                )
                self.digiit_elfatoora_submission_id = submission.id
            return True
        except Exception as e:
            raise UserError(_("Status Check Failed: %s") % str(e))

    def action_download_signed_xml(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/account.move/{self.id}/digiit_elfatoora_signed_xml/{self.digiit_elfatoora_signed_xml_filename}?download=true",
            "target": "self",
        }

    def action_download_unsigned_xml(self):
        self.ensure_one()
        generator = TEIFGenerator(self)
        xml_content = generator.generate_xml()

        filename = f"{self.name}_unsigned.xml".replace("/", "_")
        attachment = self.env["ir.attachment"].create(
            {
                "name": filename,
                "type": "binary",
                "datas": base64.b64encode(xml_content),
                "res_model": "account.move",
                "res_id": self.id,
                "mimetype": "application/xml",
            }
        )

        return {"type": "ir.actions.act_url", "url": f"/web/content/{attachment.id}?download=true", "target": "self"}

    def action_download_ttn_xml(self):
        self.ensure_one()
        if not self.digiit_elfatoora_ttn_xml:
            raise UserError(_("No TTN XML available. Please check the invoice status first."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/account.move/{self.id}/digiit_elfatoora_ttn_xml/{self.digiit_elfatoora_ttn_xml_filename}?download=true",
            "target": "self",
        }

    def _action_check_xml_signature_common(self, xml_bytes, context_label, source_code):
        self.ensure_one()
        signer = DigitalSignature()
        try:
            summary = signer.verify_xml_signature(xml_bytes)
        except UserError as e:
            summary = _("Signature verification failed:\n%s") % (e.name or str(e))

        wizard = self.env["digiit_elfatoora.signature_check_wizard"].create(
            {
                "invoice_id": self.id,
                "source": source_code,
                "result_text": summary,
            }
        )

        return {
            "name": _("Signature Check (%s)") % context_label,
            "type": "ir.actions.act_window",
            "res_model": "digiit_elfatoora.signature_check_wizard",
            "view_mode": "form",
            "target": "new",
            "res_id": wizard.id,
        }

    def action_check_signed_xml_signature(self):
        self.ensure_one()
        if not self.digiit_elfatoora_signed_xml:
            raise UserError(_("No signed XML found on this invoice."))
        xml_bytes = base64.b64decode(self.digiit_elfatoora_signed_xml)
        return self._action_check_xml_signature_common(xml_bytes, _("Signed XML"), "signed")

    def action_check_ttn_xml_signature(self):
        self.ensure_one()
        if not self.digiit_elfatoora_ttn_xml:
            raise UserError(_("No TTN XML is stored on this invoice. Please retrieve status from TTN first."))
        xml_bytes = base64.b64decode(self.digiit_elfatoora_ttn_xml)
        return self._action_check_xml_signature_common(xml_bytes, _("TTN XML"), "ttn")

