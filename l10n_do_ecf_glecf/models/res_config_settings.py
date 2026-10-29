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
    glecf_account_name = fields.Char(related="company_id.glecf_account_name")
    glecf_account_rnc = fields.Char(related="company_id.glecf_account_rnc")
    glecf_certificate_name = fields.Char(related="company_id.glecf_certificate_name")
    glecf_certificate_valid_until = fields.Date(related="company_id.glecf_certificate_valid_until")
    glecf_sequences_to_check = fields.Integer(related="company_id.glecf_sequences_to_check")
    glecf_plan_summary = fields.Char(related="company_id.glecf_plan_summary")
    glecf_plan_low = fields.Boolean(related="company_id.glecf_plan_low")
    glecf_plan_label = fields.Char(related="company_id.glecf_plan_label")
    glecf_plan_usage = fields.Char(related="company_id.glecf_plan_usage")
    glecf_issuer_state = fields.Selection(related="company_id.glecf_issuer_state")
    glecf_receive = fields.Boolean(related="company_id.glecf_receive", readonly=False)
    glecf_received_journal_id = fields.Many2one(
        related="company_id.glecf_received_journal_id", readonly=False,
    )

    def action_glecf_connect(self):
        self.ensure_one()
        return self.company_id.action_glecf_connect()

    def action_glecf_create_account(self):
        self.ensure_one()
        return self.company_id.action_glecf_create_account()

    def action_glecf_sequences(self):
        self.ensure_one()
        return self.company_id.action_glecf_sequences()

    def action_glecf_fetch_received(self):
        self.ensure_one()
        return self.company_id.action_glecf_fetch_received()

    def action_glecf_check_connection(self):
        self.ensure_one()
        return self.company_id.action_glecf_check_connection()
