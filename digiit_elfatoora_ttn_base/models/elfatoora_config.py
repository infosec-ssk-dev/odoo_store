from odoo import models, fields, api, _


class ElfatooraConfig(models.Model):
    _name = "digiit_model_elfatoora.config"
    _description = "Elfatoora Configuration"
    _inherit = ["mail.thread"]
    _check_company_auto = True

    _company_id_unique = models.Constraint(
        "UNIQUE(company_id)",
        "Only one Elfatoora Configuration is allowed per company.",
    )

    name = fields.Char(
        string="Name",
        required=True,
        default=lambda self: _("Elfatoora Config - %s") % self.env.company.name,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
    )

    company_vat = fields.Char(
        string="Company VAT / Matricule Fiscale",
        tracking=True,
        help="Company's VAT/Matricule Fiscale number. Required for Elfatoora invoice submission.",
    )

    enable_digigo = fields.Boolean(string="DigiGo", default=False, help="Sign using DigiGo Mobile/Cloud signature.")
    enable_enterprise = fields.Boolean(string="Cachet électronique Enterprise-ID", help="Sign using Server-File Certificate (Cachet Entreprise).")
    enable_token = fields.Boolean(string="Certificat ID-Trust (USB)", help="Sign using Physical USB Token.")

    digigo_available = fields.Boolean(compute="_compute_signing_provider_availability", store=False)
    enterprise_available = fields.Boolean(compute="_compute_signing_provider_availability", store=False)
    token_available = fields.Boolean(compute="_compute_signing_provider_availability", store=False)

    digigo_enabled_effective = fields.Boolean(compute="_compute_signing_provider_effective", store=False)
    enterprise_enabled_effective = fields.Boolean(compute="_compute_signing_provider_effective", store=False)
    token_enabled_effective = fields.Boolean(compute="_compute_signing_provider_effective", store=False)

    submission_method = fields.Selection(
        [
            ("webservice", "Web Service (SOAP)"),
            ("ftp", "FTP"),
        ],
        string="Submission Method",
        default="webservice",
        readonly=True,
    )

    api_url = fields.Char(
        string="TTN API WSDL URL",
        help="WSDL URL for Elfatoora web services.",
        default="https://elfatoora.tn/ElfatouraServices/EfactService?wsdl",
        tracking=True,
        required=True,
    )

    api_username = fields.Char(string="API Username (Login)", help="TTN username/login provided after subscription. Required for production.", tracking=True)
    api_password = fields.Char(string="API Password", help="TTN password provided after subscription. Required for production.")

    soap_operation_submit = fields.Char(
        string="Submit Operation Name",
        help="SOAP operation name for submitting invoices. Default: 'saveEfact' (from TTN WSDL)",
        default="saveEfact",
        tracking=True,
    )
    soap_operation_check_status = fields.Char(
        string="Check Status Operation Name",
        help="SOAP operation name for checking invoice status and downloading validated XML. Default: 'consultEfact' (from TTN WSDL).",
        default="consultEfact",
        tracking=True,
    )

    @api.model
    def _is_module_installed(self, module_name):
        return bool(self.env["ir.module.module"].sudo().search_count([("name", "=", module_name), ("state", "=", "installed")]))

    @api.depends()
    def _compute_signing_provider_availability(self):
        digigo_installed = self._is_module_installed("digiit_elfatoora_ttn_digigo")
        enterprise_installed = self._is_module_installed("digiit_elfatoora_ttn_enterprise")
        token_installed = self._is_module_installed("digiit_elfatoora_ttn_idtrust")
        for config in self:
            config.digigo_available = digigo_installed
            config.enterprise_available = enterprise_installed
            config.token_available = token_installed

    @api.depends("enable_digigo", "enable_enterprise", "enable_token")
    def _compute_signing_provider_effective(self):
        for config in self:
            config.digigo_enabled_effective = bool(config.enable_digigo) and bool(config.digigo_available)
            config.enterprise_enabled_effective = bool(config.enable_enterprise) and bool(config.enterprise_available)
            config.token_enabled_effective = bool(config.enable_token) and bool(config.token_available)

