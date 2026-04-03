import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

from ..services.teif_generator import TEIFGenerator

_logger = logging.getLogger(__name__)


class ElfatooraSubmissionWizard(models.TransientModel):
    _name = "digiit_model_elfatoora.submission_wizard"
    _description = "Elfatoora Submission Wizard"

    invoice_id = fields.Many2one("account.move", string="Invoice", required=True, readonly=True)
    company_id = fields.Many2one("res.company", string="Company", required=True, readonly=True)

    show_digigo = fields.Boolean()
    show_enterprise = fields.Boolean()
    show_token = fields.Boolean()

    signing_method = fields.Selection(
        [
            ("digigo", "DigiGo"),
            ("token", "Certificat ID-Trust (USB)"),
            ("enterprise", "Cachet électronique Enterprise-ID"),
        ],
        string="Signing Method",
    )

    unsigned_xml = fields.Text(string="XML to Sign", readonly=True)
    show_unsigned_xml = fields.Boolean(string="Show XML", default=False)

    idtrust_signing_trigger = fields.Boolean(default=False)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        invoice_id = self.env.context.get("default_invoice_id")
        if not invoice_id and self.env.context.get("active_model") == "account.move":
            invoice_id = self.env.context.get("active_id")

        if invoice_id:
            invoice = self.env["account.move"].browse(invoice_id)
            res["invoice_id"] = invoice.id
            res["company_id"] = invoice.company_id.id

            try:
                generator = TEIFGenerator(invoice)
                xml_content = generator.generate_xml()
                res["unsigned_xml"] = xml_content.decode("utf-8") if isinstance(xml_content, bytes) else xml_content
            except Exception as e:
                res["unsigned_xml"] = f"Error generating XML: {str(e)}"

            config = self.env["digiit_model_elfatoora.config"].search([("company_id", "=", invoice.company_id.id)], limit=1)
            if config:
                digigo_ok = bool(config.digigo_enabled_effective)
                enterprise_ok = bool(config.enterprise_enabled_effective)
                token_ok = bool(config.token_enabled_effective)

                res["show_digigo"] = digigo_ok
                res["show_enterprise"] = enterprise_ok
                res["show_token"] = token_ok

                enabled = {m for m, flag in (("digigo", digigo_ok), ("enterprise", enterprise_ok), ("token", token_ok)) if flag}
                last_method = self.env.user.with_company(invoice.company_id.id).last_signing_method
                if last_method and last_method in enabled:
                    res["signing_method"] = last_method
                else:
                    if digigo_ok:
                        res["signing_method"] = "digigo"
                    elif token_ok:
                        res["signing_method"] = "token"
                    elif enterprise_ok:
                        res["signing_method"] = "enterprise"

        return res

    def _persist_last_signing_method(self):
        method = self.signing_method
        company_id = self.company_id.id
        try:
            with self.pool.cursor() as new_cr:
                new_env = self.env(cr=new_cr)
                new_env.user.with_company(company_id).last_signing_method = method
        except Exception as e:
            _logger.warning("Failed to save last signing method preference: %s", e)

    def action_submit(self):
        self.ensure_one()
        self._persist_last_signing_method()

        if self.signing_method == "enterprise":
            try:
                self.invoice_id._submit_with_enterprise_cert()
            except AttributeError:
                raise UserError(_("Enterprise-ID provider is not installed. Please install addon 'digiit_elfatoora_ttn_enterprise'."))
        elif self.signing_method == "token":
            return {"type": "ir.actions.client", "tag": "idtrust_start_signing", "params": {"invoice_id": self.invoice_id.id}}
        elif self.signing_method == "digigo":
            try:
                return self.invoice_id._submit_with_digigo(signer_email=None)
            except AttributeError:
                raise UserError(_("DigiGo provider is not installed. Please install addon 'digiit_elfatoora_ttn_digigo'."))
        else:
            raise UserError(_("Please select a signing method."))

        return {"type": "ir.actions.client", "tag": "reload"}

