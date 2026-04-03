from odoo import models, _
from odoo.exceptions import UserError

import base64
import logging
from lxml import etree

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    def _submit_with_digigo(self, signer_email=None):
        self.ensure_one()

        from odoo.addons.digiit_elfatoora_ttn_base.services.teif_generator import TEIFGenerator
        from odoo.addons.digiit_elfatoora_ttn_base.services.digital_signature import DigitalSignature
        from odoo.addons.digiit_elfatoora_ttn_digigo.services.digigo_api import DigigoAPI

        generator = TEIFGenerator(self)
        xml_content = generator.generate_xml()

        config = self.env["digiit_model_elfatoora.config"].search([("company_id", "=", self.company_id.id)], limit=1)
        if not config:
            raise UserError(_("Elfatoora configuration not found for this company."))

        api = DigigoAPI(config)

        credential_id = signer_email or self.env.user.email
        if not credential_id:
            raise UserError(_("No email address found for DigiGo Signer. Please set a signer email or ensure your user has an email."))

        public_cert_b64 = api.get_certificate(credential_id)

        signer = DigitalSignature()
        digest_to_sign_bytes, context = signer.prepare_remote_signing(xml_content, public_cert_b64)
        hash_b64 = base64.b64encode(digest_to_sign_bytes).decode()

        prepared_xml = etree.tostring(context["root"], pretty_print=False, encoding="UTF-8", xml_declaration=True).decode("utf-8")

        self.write(
            {
                "pending_signing_hash": hash_b64,
                "pending_signing_xml": prepared_xml,
                "pending_signing_credential_id": credential_id,
            }
        )

        state = f"invoice_{self.id}"
        auth_url = api.get_authorize_url(hash_b64, state)

        from odoo.http import request

        if request:
            request.session["digigo_invoice_id"] = self.id

        return {"type": "ir.actions.act_url", "url": auth_url, "target": "self"}

    def _process_digigo_callback(self, code=None, sad=None):
        self.ensure_one()

        if not self.pending_signing_hash or not self.pending_signing_xml:
            raise UserError(_("No pending DigiGo transaction found for this invoice."))

        from odoo.addons.digiit_elfatoora_ttn_base.services.digital_signature import DigitalSignature
        from odoo.addons.digiit_elfatoora_ttn_digigo.services.digigo_api import DigigoAPI

        config = self.env["digiit_model_elfatoora.config"].search([("company_id", "=", self.company_id.id)], limit=1)
        if not config:
            raise UserError(_("Elfatoora configuration not found for this company."))

        api = DigigoAPI(config)

        credential_id = self.pending_signing_credential_id
        actual_code = code

        if sad:
            try:
                parts = sad.split(".")
                if len(parts) == 3:
                    import json

                    payload = parts[1]
                    payload += "=" * (-len(payload) % 4)
                    decoded_bytes = base64.urlsafe_b64decode(payload)
                    decoded = json.loads(decoded_bytes)
                    actual_code = decoded.get("jti")
            except Exception as e:
                _logger.error("Manual JWT Decode Failed: %s", e)

        if not actual_code:
            raise UserError(_("No authorization data (Code) could be extracted."))

        token_data = api.exchange_code_for_sad(actual_code)
        sad_token = token_data.get("sad")
        credential_id = token_data.get("credentialId") or token_data.get("userId") or credential_id
        if not credential_id:
            credential_id = self.env.user.email

        sig_result = api.sign_hash(sad_token, self.pending_signing_hash, credential_id)
        signature_value = sig_result.get("signature") if isinstance(sig_result, dict) else sig_result
        if not signature_value:
            extra = sig_result.get("response") if isinstance(sig_result, dict) else str(sig_result)
            raise UserError(_("No signature value returned from DigiGo. API Response: %s") % extra)

        signer = DigitalSignature()
        signed_xml = signer.embed_signature(self.pending_signing_xml, signature_value)

        self._save_signed_submission(signed_xml, config, extra_response=str(sig_result))

        self.write({"pending_signing_hash": False, "pending_signing_xml": False, "pending_signing_credential_id": False})

