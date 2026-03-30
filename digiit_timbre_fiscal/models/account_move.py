from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    digiit_timbre_fiscal = fields.Boolean(
        string='Timbre Fiscal',
        default=True,
        help='Include timbre fiscal in journal items'
    )

    invoice_line_ids = fields.One2many(
        comodel_name='account.move.line',
        inverse_name='move_id',
        domain=[
            ('display_type', 'in', ('product', 'line_section', 'line_note')),
            ('digiit_exclude_from_invoice_display', '=', False),
        ],
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            partner = vals.get('partner_id') and self.env['res.partner'].browse(vals['partner_id'])
            if partner and partner.digiit_exemption_timbre_fiscal:
                vals['digiit_timbre_fiscal'] = False
        moves = super(AccountMove, self).create(vals_list)
        for move in moves:
            if move.digiit_timbre_fiscal and not move._is_partner_timbre_exempt():
                move._add_timbre_fiscal_line()
        return moves

    def write(self, vals):
        if 'partner_id' in vals and vals['partner_id']:
            partner = self.env['res.partner'].browse(vals['partner_id'])
            if partner.digiit_exemption_timbre_fiscal:
                vals['digiit_timbre_fiscal'] = False
        res = super(AccountMove, self).write(vals)
        if 'digiit_timbre_fiscal' in vals or 'partner_id' in vals:
            for move in self:
                if move.digiit_timbre_fiscal and not move._is_partner_timbre_exempt():
                    # Add timbre fiscal line if it doesn't exist
                    if not move._has_timbre_fiscal_line():
                        move._add_timbre_fiscal_line()
                else:
                    # Remove timbre fiscal line (either disabled or partner exempt)
                    move._remove_timbre_fiscal_line()
        return res

    def _has_timbre_fiscal_line(self):
        """Check if move already has a timbre fiscal line"""
        return any(line.digiit_is_timbre_fiscal for line in self.line_ids)

    def _is_partner_timbre_exempt(self):
        """Check if the specific partner on the invoice is exempt from timbre fiscal"""
        return self.partner_id and self.partner_id.digiit_exemption_timbre_fiscal

    def _add_timbre_fiscal_line(self):
        """Add timbre fiscal line to the move"""
        # Skip if already exists
        if self._has_timbre_fiscal_line():
            return

        # Skip if partner is exempt from timbre fiscal
        if self._is_partner_timbre_exempt():
            return

        # Determine tax type and find timbre fiscal tax first
        tax_use_type = 'none'
        if self.move_type in ('out_invoice', 'out_refund', 'out_receipt'):
            tax_use_type = 'sale'
        elif self.move_type in ('in_invoice', 'in_refund', 'in_receipt'):
            tax_use_type = 'purchase'

        timbre_tax = self.env['account.tax'].search([
            ('digiit_is_timbre_fiscal_tax', '=', True),
            ('type_tax_use', '=', tax_use_type),
            ('company_id', '=', self.company_id.id),
        ], limit=1)

        if not timbre_tax:
            timbre_tax = self.env['account.tax'].search([
                ('digiit_is_timbre_fiscal_tax', '=', True),
                ('type_tax_use', '=', 'none'),
                ('company_id', '=', self.company_id.id),
            ], limit=1)

        if not timbre_tax:
            return

        # Use account from tax repartition (same as the tax), fallback to chart of accounts
        is_refund = self.move_type in ('out_refund', 'in_refund')
        repartition = timbre_tax.refund_repartition_line_ids if is_refund else timbre_tax.invoice_repartition_line_ids
        tax_line = repartition.filtered(lambda r: r.repartition_type == 'tax')[:1]
        timbre_account = tax_line.account_id

        if not timbre_account:
            account_domain = [('company_ids', 'in', self.company_id.id)]
            if tax_use_type == 'sale':
                timbre_account = self.env['account.account'].search(
                    account_domain + [('digiit_is_timbre_fiscal_sale_account', '=', True)], limit=1
                )
            elif tax_use_type == 'purchase':
                timbre_account = self.env['account.account'].search(
                    account_domain + [('digiit_is_timbre_fiscal_purchase_account', '=', True)], limit=1
                )
            if not timbre_account:
                timbre_account = self.env['account.account'].search(
                    account_domain + [('digiit_is_timbre_fiscal_account', '=', True)], limit=1
                )

        if not timbre_account:
            return

        # Create ONE product line with price_unit=0 and tax
        # This will create the tax amount automatically
        self.env['account.move.line'].with_context(check_move_validity=False).create({
            'move_id': self.id,
            'account_id': timbre_account.id,
            'name': 'Timbre Fiscal',
            'quantity': 1,
            'price_unit': 0.0,  # Zero price, tax will add the amount
            'digiit_is_timbre_fiscal': True,
            'digiit_exclude_from_invoice_display': True,  # Hidden from invoice lines
            'tax_ids': [(6, 0, [timbre_tax.id])],  # This will create the tax amount
        })

    def _remove_timbre_fiscal_line(self):
        """Remove timbre fiscal line from the move"""
        timbre_lines = self.line_ids.filtered(lambda l: l.digiit_is_timbre_fiscal)
        if timbre_lines:
            timbre_lines.with_context(check_move_validity=False).unlink()