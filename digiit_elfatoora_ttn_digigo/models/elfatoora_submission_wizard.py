import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ElfatooraSubmissionWizard(models.TransientModel):
    _inherit = "digiit_model_elfatoora.submission_wizard"

    digigo_signer_id = fields.Many2one(
        "digiit.model.digigo.signer",
        string="Signer (Email)",
        domain="[('config_id.company_id', '=', company_id)]",
    )
    save_as_default = fields.Boolean(string="Remember as default", default=False)
    has_multiple_signers = fields.Boolean(default=False)

    @api.onchange("signing_method")
    def _onchange_signing_method_digigo(self):
        if self.signing_method != "digigo":
            return

        company_id = self.company_id.id if self.company_id else self.env.company.id
        signers = self.env["digiit.model.digigo.signer"].search([("company_id", "=", company_id)])
        self.has_multiple_signers = len(signers) > 1

        default_signer = self.env.user.with_company(company_id).digigo_default_signer_id
        if default_signer and default_signer in signers:
            self.digigo_signer_id = default_signer
            self.save_as_default = True
        elif len(signers) == 1:
            self.digigo_signer_id = signers
            self.save_as_default = False
        else:
            self.digigo_signer_id = False
            self.save_as_default = False

    @api.onchange("digigo_signer_id")
    def _onchange_digigo_signer_id(self):
        """Match original behavior: changing signer unchecks remember-as-default unless it matches current default."""
        if self.signing_method != "digigo" or not self.digigo_signer_id:
            return
        current_default = self.env.user.with_company(self.company_id.id).digigo_default_signer_id
        self.save_as_default = bool(current_default and current_default == self.digigo_signer_id)

    def action_submit(self):
        self.ensure_one()

        # DigiGo branch handled here (provider-specific UI + signer selection)
        if self.signing_method == "digigo":
            self._persist_last_signing_method()

            if not self.digigo_signer_id:
                raise UserError(_("Please select an authorized signer for DigiGo."))

            if self.save_as_default:
                try:
                    # Use a separate cursor so this preference survives rollback,
                    # and avoid concurrent writes on res.users in the main txn.
                    with self.pool.cursor() as new_cr:
                        new_env = self.env(cr=new_cr)
                        new_env.user.with_company(self.company_id.id).digigo_default_signer_id = self.digigo_signer_id.id
                except Exception:
                    _logger.warning("Failed to persist default DigiGo signer preference.", exc_info=True)

            return self.invoice_id._submit_with_digigo(signer_email=self.digigo_signer_id.name)

        return super().action_submit()

