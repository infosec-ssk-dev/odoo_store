from odoo import SUPERUSER_ID


def uninstall_hook(env):
    """
    Purge binary attachments bound to base binary fields on account.move
    """
    env = env(user=SUPERUSER_ID)

    # Remove binary attachments linked to base binary fields on invoices.
    env["ir.attachment"].sudo().search(
        [
            ("res_model", "=", "account.move"),
            (
                "res_field",
                "in",
                [
                    "digiit_elfatoora_signed_xml",
                    "digiit_elfatoora_ttn_xml",
                    "digiit_elfatoora_qr_code",
                ],
            ),
        ]
    ).unlink()

