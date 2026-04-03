from odoo import models, fields, api


class ElfatooraSubmission(models.Model):
    _name = "digiit_model_elfatoora.submission"
    _description = "Elfatoora Submission History"
    _order = "create_date desc"
    _check_company_auto = True

    name = fields.Char(string="Reference", required=True, copy=False, readonly=True, default="New")
    invoice_id = fields.Many2one("account.move", string="Invoice", required=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", string="Company", related="invoice_id.company_id", store=True)

    processing_date = fields.Datetime(string="Processing Date", readonly=True)
    validation_date = fields.Datetime(string="Validation Date", readonly=True)

    status = fields.Selection(
        [
            ("ack", "Received by TTN"),
            ("pending", "Pending"),
            ("validated", "Validated"),
            ("rejected", "Rejected"),
        ],
        string="Status",
        default="ack",
        required=True,
    )

    ttn_reference = fields.Char(string="TTN Reference", readonly=True)
    tracking_number = fields.Char(string="Tracking Number", readonly=True)

    submission_method = fields.Selection(
        [
            ("webservice", "Web Service (SOAP)"),
            ("ftp", "FTP (Legacy)"),
        ],
        string="Method",
        readonly=True,
        default="webservice",
    )

    request_xml = fields.Text(string="Request XML", readonly=True)
    response_xml = fields.Text(string="Response XML", readonly=True)
    error_message = fields.Text(string="Error Message", readonly=True)

    retry_count = fields.Integer(string="Retry Count", default=0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New" and vals.get("invoice_id"):
                invoice = self.env["account.move"].browse(vals["invoice_id"])
                count = self.search_count([("invoice_id", "=", invoice.id)])
                vals["name"] = f"{invoice.name}/{count + 1}"
        return super().create(vals_list)

