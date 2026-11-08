from odoo import api, models


class AccountTax(models.Model):
    _inherit = "account.tax"

    @api.model
    def _l10n_do_tax_groups(self, company):
        """The tax groups of ITBIS and ISR of a company.

        Odoo 20 has one ITBIS group per rate and keeps the ITBIS withholdings
        in a group of their own (2026-11-01); a database that comes from an
        older version can still have the single group, `itbis`.
        """
        names = {
            "itbis": ("itbis", "itbis_18", "itbis_16", "itbis_tax_0", "itbis_0", "ret"),
            "isr": ("isr",),
        }
        groups = {}
        for key, group_names in names.items():
            groups[key] = self.env["account.tax.group"]
            for name in group_names:
                groups[key] |= self.env.ref(
                    "account.%s_tax_group_%s" % (company.id, name),
                    raise_if_not_found=False,
                ) or self.env["account.tax.group"]
        return groups
