from odoo import models, fields, api


class AccountMove(models.Model):
    _inherit = 'account.move'

    digiit_suspension_tva_active = fields.Boolean(
        string='Suspension TVA Active',
        compute='_compute_digiit_suspension_tva_active',
        store=True,
    )

    digiit_autorisation_id = fields.Many2one(
        'digiit.autorisation',
        string='Num Autorisation',
        domain="[('partner_id', '=', partner_id)]",
    )

    @api.depends('fiscal_position_id', 'fiscal_position_id.digiit_suspension_tva')
    def _compute_digiit_suspension_tva_active(self):
        for move in self:
            move.digiit_suspension_tva_active = bool(
                move.fiscal_position_id and move.fiscal_position_id.digiit_suspension_tva
            )

    def _digiit_create_bon_if_needed(self):
        Bon = self.env['digiit.bon.commande.vise']
        for move in self:
            if (
                move.move_type == 'out_invoice'
                and move.fiscal_position_id
                and move.fiscal_position_id.digiit_suspension_tva
            ):
                exists = Bon.search([('move_id', '=', move.id)], limit=1)
                if not exists:
                    Bon.create({
                        'move_id': move.id,
                        'digiit_autorisation_id': move.digiit_autorisation_id.id or False,
                    })

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._digiit_create_bon_if_needed()
        return moves

    def write(self, vals):
        res = super().write(vals)
        if 'fiscal_position_id' in vals or 'partner_id' in vals:
            self._digiit_create_bon_if_needed()
        if 'digiit_autorisation_id' in vals:
            Bon = self.env['digiit.bon.commande.vise']
            for move in self:
                bon = Bon.search([('move_id', '=', move.id)], limit=1)
                if bon:
                    bon.digiit_autorisation_id = move.digiit_autorisation_id.id or False
        return res