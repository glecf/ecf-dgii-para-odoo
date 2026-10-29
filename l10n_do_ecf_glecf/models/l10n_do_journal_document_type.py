from datetime import date

from odoo import api, fields, models

# The e-CF types a company issues itself: every sales type, and the three
# a buyer issues on a vendor bill (purchases, minor expenses, payments abroad).
PURCHASE_TYPES_ISSUED = ("E41", "E43", "E47")


class AccountJournalDocumentType(models.Model):
    """The e-NCF sequence of each e-CF type, as l10n_do_accounting keeps it.

    l10n_do_accounting fills the expiry date by itself with 31 December of the
    next year. The DGII rejects an e-CF whose sequence expiry does not match
    the authorization, so a date nobody has looked at is flagged until
    someone confirms it.
    """

    _inherit = "l10n_do.account.journal.document_type"

    glecf_expiry_confirmed = fields.Boolean(
        string="Checked",
        help="The expiry date was checked against the sequence the DGII authorized.",
    )
    glecf_expiry_unchecked = fields.Boolean(compute="_compute_glecf_expiry_unchecked")
    glecf_last_number = fields.Char(string="Last e-NCF used", compute="_compute_glecf_last_number")

    @api.model
    def _glecf_issued_domain(self, companies):
        return [
            ("company_id", "in", companies.ids),
            ("l10n_latam_document_type_id.doc_code_prefix", "=like", "E%"),
            "|",
            ("journal_id.type", "=", "sale"),
            ("l10n_latam_document_type_id.doc_code_prefix", "in", PURCHASE_TYPES_ISSUED),
        ]

    @api.depends("l10n_do_ncf_expiration_date", "glecf_expiry_confirmed")
    def _compute_glecf_expiry_unchecked(self):
        for rec in self:
            created = (rec.create_date or fields.Datetime.now()).date()
            odoo_default = date(created.year + 1, 12, 31)
            rec.glecf_expiry_unchecked = (
                not rec.glecf_expiry_confirmed and rec.l10n_do_ncf_expiration_date == odoo_default
            )

    def _compute_glecf_last_number(self):
        # l10n_do_accounting numbers each type per company, across journals.
        moves = self.env["account.move"]
        for rec in self:
            last = moves.search([
                ("company_id", "=", rec.company_id.id),
                ("l10n_latam_document_type_id", "=", rec.l10n_latam_document_type_id.id),
                ("state", "=", "posted"),
                ("l10n_do_fiscal_number", "!=", False),
            ], order="l10n_do_fiscal_number desc", limit=1)
            rec.glecf_last_number = last.l10n_do_fiscal_number or False

    def write(self, vals):
        # Whoever changes the date has looked at it.
        if "l10n_do_ncf_expiration_date" in vals and "glecf_expiry_confirmed" not in vals:
            vals = dict(vals, glecf_expiry_confirmed=True)
        return super().write(vals)
