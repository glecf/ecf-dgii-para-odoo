from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    glecf_api_key = fields.Char(
        related="company_id.glecf_api_key",
        readonly=False,
    )
    glecf_consent = fields.Boolean(
        related="company_id.glecf_consent",
        readonly=False,
    )
    glecf_auto_send = fields.Boolean(
        related="company_id.glecf_auto_send",
        readonly=False,
    )
    l10n_do_ecf_service_env = fields.Selection(
        related="company_id.l10n_do_ecf_service_env",
    )

    def action_glecf_check_connection(self):
        self.ensure_one()
        return self.company_id.action_glecf_check_connection()
