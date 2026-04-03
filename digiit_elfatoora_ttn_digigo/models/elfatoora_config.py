from odoo import models, fields


class ElfatooraConfig(models.Model):
    _inherit = "digiit_model_elfatoora.config"

    digigo_api_url = fields.Char(string="DigiGo API URL", default="https://digigo.tuntrust.tn/tunsign-proxy-webapp")
    digigo_client_id = fields.Char(string="DigiGo Client ID", help="The Client ID provided by Tuntrust for your application.")
    digigo_client_secret = fields.Char(string="DigiGo Client Secret", help="The Client Secret provided by Tuntrust.")
    digigo_redirect_uri = fields.Char(string="DigiGo Redirect URI", help="The Redirect URI registered for your generic application (e.g., https://yourdomain.com/digigo/callback).")

    signer_ids = fields.One2many("digiit.model.digigo.signer", "config_id", string="Authorized Signers")


class DigigoSigner(models.Model):
    _name = "digiit.model.digigo.signer"
    _description = "Authorized DigiGo Signer"
    _check_company_auto = True

    _email_company_uniq = models.Constraint(
        "UNIQUE(name, company_id)",
        "The email must be unique per company!",
    )

    name = fields.Char(string="Email (Credential ID)", required=True)
    config_id = fields.Many2one("digiit_model_elfatoora.config", string="Configuration", ondelete="cascade")
    company_id = fields.Many2one(related="config_id.company_id", store=True, readonly=True)

