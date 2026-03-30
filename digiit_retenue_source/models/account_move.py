from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    digiit_retenu_source_record_ids = fields.One2many(
        "digiit.retenu.source.record",
        "invoice_id",
        string="Retenue Records",
        copy=False,
    )
    digiit_retenu_source_count = fields.Integer(
        compute="_compute_digiit_retenu_source_count",
        string="Retenue Count",
    )
    digiit_has_retenu = fields.Boolean(
        compute="_compute_digiit_retenu_source_count",
        string="Has Retenue",
    )

    @api.depends("digiit_retenu_source_record_ids")
    def _compute_digiit_retenu_source_count(self):
        for move in self:
            count = len(move.digiit_retenu_source_record_ids)
            move.digiit_retenu_source_count = count
            move.digiit_has_retenu = count > 0

    def action_open_retenu_source_moves(self):
        """Smart button: since one invoice = one retenu, open the journal entry form directly."""
        self.ensure_one()
        move_id = self.digiit_retenu_source_record_ids[:1].digiit_account_move_id
        return {
            "name": _("Retenue a la Source"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": move_id.id,
            "context": {"create": False},
        }

    def action_open_retenu_source_wizard(self):
        self.ensure_one()
        if self.move_type not in ("out_invoice", "in_invoice"):
            raise UserError(_("Retenue a la source is only available for customer and vendor invoices."))
        if self.state != "posted":
            raise UserError(_("You can only create retenue a la source from a posted invoice."))
        if self.payment_state == "paid":
            raise UserError(_("The invoice is already paid."))
        if self.digiit_has_retenu:
            raise UserError(_("This invoice already has a retenue a la source record."))

        return {
            "name": _("Retenue a la Source"),
            "type": "ir.actions.act_window",
            "res_model": "digiit.retenu.source.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_invoice_id": self.id,
            },
        }

    def unlink(self):
        for invoice in self:
            posted_retenu = invoice.digiit_retenu_source_record_ids.filtered(
                lambda r: r.state == "posted"
            )
            if posted_retenu:
                raise UserError(
                    _(
                        "You cannot delete invoice '%s' because it has a posted retenue a la source record.\n"
                        "Please cancel and delete the retenue record first."
                    ) % invoice.name
                )
            draft_or_cancel_retenu = invoice.digiit_retenu_source_record_ids.filtered(
                lambda r: r.state in ("draft", "cancel")
            )
            draft_or_cancel_retenu.unlink()
        return super().unlink()