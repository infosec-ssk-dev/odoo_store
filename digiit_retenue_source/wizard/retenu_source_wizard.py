from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class DigiitRetenuSourceWizard(models.TransientModel):
    _name = "digiit.retenu.source.wizard"
    _description = "Retenue a la Source Wizard"

    invoice_id = fields.Many2one("account.move", string="Invoice", required=True, readonly=True)
    move_type = fields.Selection(
        [("sale", "Customer"), ("purchase", "Vendor")],
        string="Type",
        required=True,
        readonly=True,
    )
    payment_journal_id = fields.Many2one("account.journal", string="Payment Journal", required=True, ondelete="restrict")
    withholding_tax_id = fields.Many2one("account.tax", string="Withholding Tax", required=True, ondelete="restrict")
    withholding_number = fields.Char(string="Withholding Number")
    withholding_base_amount = fields.Monetary(string="Withholding Base Amount", readonly=True, currency_field='currency_id')
    withholding_tax_amount = fields.Monetary(
        string="Withholding Amount",
        compute="_compute_amounts",
        store=False,
        readonly=True,
        currency_field='currency_id'
    )
    payment_amount = fields.Monetary(
        string="Montant du paiement",
        compute="_compute_amounts",
        store=False,
        readonly=True,
        currency_field='currency_id',
        help="Retenue a la source amount (withheld from the invoice)",
    )
    date_reglement = fields.Date(string="Date de reglement", required=True, default=fields.Date.context_today)
    memo = fields.Char(string="Memo")
    currency_id = fields.Many2one("res.currency", required=True, readonly=True)
    digiit_allowed_payment_journal_ids = fields.Many2many(
        "account.journal",
        compute="_compute_allowed_records",
    )
    digiit_allowed_withholding_tax_ids = fields.Many2many(
        "account.tax",
        compute="_compute_allowed_records",
    )

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        invoice = self.env["account.move"].browse(self.env.context.get("default_invoice_id"))
        if invoice:
            # Base = total without timbre fiscal
            # Timbre amount is on TAX lines (tax_line_id), not on the product line (digiit_is_timbre_fiscal)
            # The product line has price_unit=0; the actual amount is on the tax line
            timbre_lines = invoice.line_ids.filtered(
                lambda l: l.tax_line_id and getattr(l.tax_line_id, "digiit_is_timbre_fiscal_tax", False)
            )
            # Use balance (company currency); amount_total is in invoice currency - same when single currency
            timbre_amount = sum(abs(l.balance) for l in timbre_lines)
            inv_total = abs(invoice.amount_total or 0)
            if invoice.currency_id == invoice.company_id.currency_id:
                base = inv_total - timbre_amount
            else:
                # Multi-currency: convert timbre from company to invoice currency
                timbre_inv_curr = invoice.company_id.currency_id._convert(
                    timbre_amount, invoice.currency_id, invoice.company_id, invoice.date
                )
                base = inv_total - abs(timbre_inv_curr)
            vals.update(
                {
                    "invoice_id": invoice.id,
                    "move_type": "sale" if invoice.move_type == "out_invoice" else "purchase",
                    "withholding_base_amount": max(0, base),
                    "currency_id": invoice.currency_id.id,
                }
            )
        return vals

    @api.depends("move_type")
    def _compute_allowed_records(self):
        Journal = self.env["account.journal"]
        Tax = self.env["account.tax"]
        for rec in self:
            if rec.move_type == "sale":
                rec.digiit_allowed_payment_journal_ids = Journal.search([("digiit_withholding_sales", "=", True)])
                rec.digiit_allowed_withholding_tax_ids = Tax.search(
                    [("type_tax_use", "=", "sale"), ("digiit_retenu_tax", "=", True), ("active", "=", True)]
                )
            elif rec.move_type == "purchase":
                rec.digiit_allowed_payment_journal_ids = Journal.search([("digiit_withholding_purchases", "=", True)])
                rec.digiit_allowed_withholding_tax_ids = Tax.search(
                    [("type_tax_use", "=", "purchase"), ("digiit_retenu_tax", "=", True), ("active", "=", True)]
                )
            else:
                rec.digiit_allowed_payment_journal_ids = Journal.browse()
                rec.digiit_allowed_withholding_tax_ids = Tax.browse()

    @api.onchange("move_type")
    def _onchange_move_type(self):
        self.payment_journal_id = False
        self.withholding_tax_id = False

    @api.onchange("payment_journal_id")
    def _onchange_payment_journal_id(self):
        self.withholding_tax_id = False

    @api.depends("withholding_base_amount", "withholding_tax_id.amount", "withholding_tax_id.amount_type")
    def _compute_amounts(self):
        for rec in self:
            tax_amount = 0.0
            if rec.withholding_tax_id and rec.withholding_tax_id.amount_type == "percent":
                tax_amount = rec.withholding_base_amount * rec.withholding_tax_id.amount / 100.0
            rec.withholding_tax_amount = tax_amount
            rec.payment_amount = tax_amount

    def action_create_retenu_record(self):
        self.ensure_one()
        invoice = self.invoice_id
        if invoice.payment_state == "paid":
            raise ValidationError(_("The invoice is already paid."))
        if self.withholding_tax_amount <= 0.0:
            raise ValidationError(_("Withholding amount must be greater than zero."))

        # Create the retenu record in draft first
        record = self.env["digiit.retenu.source.record"].create(
            {
                "company_id": invoice.company_id.id,
                "currency_id": self.currency_id.id,
                "move_type": self.move_type,
                "invoice_id": invoice.id,
                "payment_journal_id": self.payment_journal_id.id,
                "date_reglement": self.date_reglement,
                "memo": self.memo,
                "withholding_tax_id": self.withholding_tax_id.id,
                "withholding_number": self.withholding_number,
                "withholding_base_amount": self.withholding_base_amount,
                "state": "draft",
            }
        )

        # Create and post the withholding journal entry
        digiit_withholding_move = self._create_digiit_withholding_move(invoice)
        record.digiit_account_move_id = digiit_withholding_move.id

        # Mark the retenu record as posted now that the journal entry is confirmed
        record.action_post()

        return {
            "name": _("Retenue a la Source"),
            "type": "ir.actions.act_window",
            "res_model": "digiit.retenu.source.record",
            "res_id": record.id,
            "view_mode": "form",
            "target": "current",
        }

    def _get_digiit_invoice_partner_line(self, invoice):
        if self.move_type == "sale":
            line = invoice.line_ids.filtered(
                lambda l: l.account_id.account_type == "asset_receivable" and not l.reconciled
            )[:1]
        else:
            line = invoice.line_ids.filtered(
                lambda l: l.account_id.account_type == "liability_payable" and not l.reconciled
            )[:1]
        if not line:
            raise UserError(_("No open receivable/payable line found on the invoice."))
        return line

    def _create_digiit_withholding_move(self, invoice):
        """Create a separate journal entry (ecriture) for retenue a la source in the chosen journal."""
        self.ensure_one()
        partner_line = self._get_digiit_invoice_partner_line(invoice)
        journal = self.payment_journal_id
        journal_account = journal.default_account_id
        if not journal_account:
            raise UserError(
                _("Please configure a Default Account on journal %s to book retenue a la source.") % journal.display_name
            )

        company = invoice.company_id
        company_currency = company.currency_id
        amount_currency = self.withholding_tax_amount
        amount_company = self.currency_id._convert(amount_currency, company_currency, company, self.date_reglement)

        if abs(amount_company) < 10 ** (-company.currency_id.decimal_places - 1):
            raise UserError(_("Withholding amount converts to zero in company currency."))

        if self.move_type == "sale":
            partner_debit = 0.0
            partner_credit = amount_company
            journal_debit = amount_company
            journal_credit = 0.0
            partner_amount_currency = -amount_currency
            journal_amount_currency = amount_currency
        else:
            partner_debit = amount_company
            partner_credit = 0.0
            journal_debit = 0.0
            journal_credit = amount_company
            partner_amount_currency = amount_currency
            journal_amount_currency = -amount_currency

        line_currency = self.currency_id if self.currency_id != company_currency else company_currency

        move_vals = {
            "move_type": "entry",
            "journal_id": journal.id,
            "date": self.date_reglement,
            "ref": "%s - %s" % (invoice.name or invoice.ref or invoice.display_name, self.withholding_number),
            "company_id": company.id,
            "line_ids": [
                (
                    0,
                    0,
                    {
                        "name": _("Retenue a la source %s") % (invoice.name or invoice.ref or ""),
                        "partner_id": invoice.partner_id.id,
                        "account_id": partner_line.account_id.id,
                        "debit": partner_debit,
                        "credit": partner_credit,
                        "currency_id": line_currency.id,
                        "amount_currency": partner_amount_currency,
                    },
                ),
                (
                    0,
                    0,
                    {
                        "name": _("Retenue a la source %s") % self.withholding_number,
                        "partner_id": invoice.partner_id.id,
                        "account_id": journal_account.id,
                        "debit": journal_debit,
                        "credit": journal_credit,
                        "currency_id": line_currency.id,
                        "amount_currency": journal_amount_currency,
                    },
                ),
            ],
        }
        move = self.env["account.move"].create(move_vals)
        move.action_post()

        retenue_partner_line = move.line_ids.filtered(
            lambda l: l.account_id == partner_line.account_id and not l.reconciled
        )[:1]
        (partner_line + retenue_partner_line).reconcile()
        return move