from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DigiitRetenuSourceRecord(models.Model):
    _name = "digiit.retenu.source.record"
    _description = "Retenue a la Source Record"
    _order = "date_reglement desc, id desc"

    name = fields.Char(
        string="Reference",
        required=True,
        copy=False,
        default="/",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("posted", "Posted"),
            ("cancel", "Cancelled"),
        ],
        string="Status",
        default="draft",
        required=True,
        copy=False,
        tracking=True,
    )
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one("res.currency", required=True, default=lambda self: self.env.company.currency_id)
    move_type = fields.Selection(
        [("sale", "Customer"), ("purchase", "Vendor")],
        string="Type",
        required=True,
    )
    invoice_id = fields.Many2one("account.move", string="Invoice", required=True, ondelete="restrict")
    digiit_account_move_id = fields.Many2one(
        "account.move", string="Withholding Entry", readonly=True, ondelete="restrict"
    )
    partner_id = fields.Many2one("res.partner", string="Partner", related="invoice_id.partner_id", store=True)
    payment_journal_id = fields.Many2one("account.journal", string="Payment Journal", required=True, ondelete="restrict")
    date_reglement = fields.Date(string="Date de reglement", required=True)
    memo = fields.Char(string="Memo")
    withholding_tax_id = fields.Many2one("account.tax", string="Withholding Tax", required=True, ondelete="restrict")
    withholding_number = fields.Char(string="Withholding Number")
    withholding_base_amount = fields.Monetary(string="Withholding Base Amount", required=True, currency_field='currency_id')
    withholding_tax_amount = fields.Monetary(
        string="Withholding Amount",
        compute="_compute_amounts",
        store=True,
        currency_field='currency_id'
    )
    payment_amount = fields.Monetary(
        string="Montant du paiement",
        compute="_compute_amounts",
        store=True,
        currency_field='currency_id',
        help="Retenue a la source amount (withheld, deducted from base)",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                move_type = vals.get("move_type", "sale")
                code = "digiit.retenu.source.record.sale" if move_type == "sale" else "digiit.retenu.source.record.purchase"
                vals["name"] = self.env["ir.sequence"].next_by_code(code) or "New"
        return super().create(vals_list)

    @api.depends("withholding_base_amount", "withholding_tax_id.amount", "withholding_tax_id.amount_type")
    def _compute_amounts(self):
        for rec in self:
            tax_amount = 0.0
            if rec.withholding_tax_id and rec.withholding_tax_id.amount_type == "percent":
                tax_amount = rec.withholding_base_amount * rec.withholding_tax_id.amount / 100.0
            rec.withholding_tax_amount = tax_amount
            rec.payment_amount = tax_amount

    def action_post(self):
        """Confirm the retenu: post the related journal entry if not already posted."""
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft retenue records can be confirmed."))
            if not rec.digiit_account_move_id:
                raise UserError(_("No withholding journal entry found. Cannot confirm."))
            # Re-post the journal entry if it was reset to draft during a cancel/reset cycle
            if rec.digiit_account_move_id.state == "draft":
                rec.digiit_account_move_id.action_post()
            rec.state = "posted"

    def action_reset_to_draft_posted(self):
        """Reset posted retenu to draft (same flow as payment): unreconcile, void the move, set draft."""
        for rec in self:
            if rec.state != "posted":
                raise UserError(_("Only posted retenue records can be reset to draft."))
            move = rec.digiit_account_move_id
            if move and move.state == "posted":
                move.line_ids.filtered(lambda l: l.reconciled).remove_move_reconcile()
                move.button_draft()
            rec.state = "draft"

    def action_reset_to_draft(self):
        """Reset a cancelled retenu back to draft."""
        for rec in self:
            if rec.state != "cancel":
                raise UserError(_("Only cancelled retenue records can be reset to draft."))
            rec.write({"digiit_account_move_id": False, "state": "draft"})

    def unlink(self):
        for rec in self:
            if rec.state == "posted":
                raise UserError(_("Vous ne pouvez pas supprimer une retenue comptabilisée. Veuillez d'abord la remettre en brouillon."))
            move = rec.digiit_account_move_id
            if move and move.state == "draft":
                rec.digiit_account_move_id = False
                move.unlink()
        return super().unlink()

    def action_open_withholding_entry(self):
        self.ensure_one()
        return {
            "name": _("Withholding Entry"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.digiit_account_move_id.id,
            "context": {"create": False},
        }