from urllib.parse import urlencode

from odoo import _, fields, models
from odoo.exceptions import UserError

from .glecf_client import GlecfClient, GlecfError

REGISTER_URL = "https://glecf.com/register"

# GLECF environment -> DGII environment, as the localization expects it in
# `l10n_do_ecf_service_env` to build the QR code of the printed invoice.
DGII_ENVIRONMENTS = {
    "testecf": "TesteCF",
    "certecf": "CerteCF",
    "ecf": "eCF",
}


class ResCompany(models.Model):
    _inherit = "res.company"

    glecf_api_key = fields.Char(
        string="GLECF API key",
        groups="base.group_system",
        copy=False,
    )
    glecf_consent = fields.Boolean(
        string="Send my invoices to GLECF",
        copy=False,
        help="Your e-CF invoices are sent to GLECF, which signs them with your "
        "certificate and delivers them to the DGII. Nothing is sent until "
        "this is checked.",
    )
    glecf_auto_send = fields.Boolean(
        string="Send e-CF when posting",
        default=True,
        help="Send each e-CF a moment after it is posted. If unchecked, use "
        "the «Send to DGII» button on the invoice.",
    )
    l10n_do_ecf_service_env = fields.Selection(
        selection=[
            ("TesteCF", "Test"),
            ("CerteCF", "Certification"),
            ("eCF", "Production"),
        ],
        string="DGII environment",
        default="TesteCF",
        readonly=True,
        help="Set by your GLECF API key: each key belongs to one environment. "
        "It is updated with every invoice GLECF processes.",
    )

    # Only what helps to recognise the certificate. The certificate itself and
    # its password are kept by GLECF, never in Odoo.
    glecf_certificate_name = fields.Char(string="Certificate holder", readonly=True, copy=False)
    glecf_certificate_valid_until = fields.Date(string="Certificate valid until", readonly=True, copy=False)

    # The GLECF company the API key belongs to, read when connecting.
    glecf_account_name = fields.Char(string="GLECF account", readonly=True, copy=False)
    glecf_account_rnc = fields.Char(string="GLECF account RNC", readonly=True, copy=False)

    def write(self, vals):
        if "glecf_api_key" in vals:
            # Another key may belong to another company: read it again.
            vals = dict(vals, glecf_account_name=False, glecf_account_rnc=False)
        return super().write(vals)

    def _glecf_odoo_rnc(self):
        return "".join(char for char in (self.vat or "") if char.isdigit())

    def _glecf_refresh_account(self):
        """Read which GLECF company and environment the API key belongs to."""
        account = self._glecf_client().current_account()
        self.sudo().write({
            "glecf_account_name": account.get("name") or False,
            "glecf_account_rnc": account.get("rnc") or False,
        })
        self._glecf_set_environment(account.get("environment"))
        return account

    def _glecf_rnc_mismatch_message(self):
        """Why sending would fail, if the GLECF account is another company."""
        if self.glecf_account_rnc and self.glecf_account_rnc != self._glecf_odoo_rnc():
            return _(
                "The GLECF account of this API key is %(account)s, RNC %(glecf)s, but the RNC of "
                "%(company)s in Odoo is %(odoo)s. The DGII would reject every invoice: use the API "
                "key of this company's GLECF account, or correct the RNC in Odoo."
            ) % {
                "account": self.glecf_account_name or "-",
                "glecf": self.glecf_account_rnc,
                "company": self.name,
                "odoo": self._glecf_odoo_rnc() or "-",
            }
        return False

    def action_glecf_create_account(self):
        """Open the GLECF sign-up with this company's data already filled in."""
        self.ensure_one()
        address = ", ".join(filter(None, [self.street, self.street2, self.city]))
        params = {
            "origen": "odoo",
            "empresa": self.name or "",
            "rnc": self._glecf_odoo_rnc(),
            "direccion": address,
            "telefono": self.phone or "",
            "correo": self.email or "",
        }
        return {
            "type": "ir.actions.act_url",
            "url": "%s?%s" % (REGISTER_URL, urlencode({k: v for k, v in params.items() if v})),
            "target": "new",
        }

    def _glecf_store_certificate(self, certificate):
        """Remember who the certificate in use belongs to and its expiry."""
        self.ensure_one()
        valid_until = (certificate or {}).get("valid_until")
        self.write({
            "glecf_certificate_name": _certificate_holder((certificate or {}).get("subject")),
            "glecf_certificate_valid_until": valid_until[:10] if valid_until else False,
        })

    def _glecf_refresh_certificate(self):
        certificates = self._glecf_client().list_certificates()
        in_use = [c for c in certificates if c.get("is_default") and c.get("is_active")]
        self.sudo()._glecf_store_certificate(in_use[0] if in_use else None)

    def _glecf_client(self):
        self.ensure_one()
        api_url = self.env["ir.config_parameter"].sudo().get_param("glecf.api_url")
        return GlecfClient(self.sudo().glecf_api_key, api_url or None)

    def _glecf_check_ready(self):
        """Raise a clear error if this company cannot send e-CF yet."""
        self.ensure_one()
        if not self.sudo().glecf_api_key:
            raise UserError(
                _("Connect %s to GLECF first: Settings > Invoicing > Electronic invoicing.") % self.name
            )
        if not self.glecf_consent:
            raise UserError(
                _("%s has not accepted sending its invoices to GLECF yet (Settings > Invoicing).") % self.name
            )
        if not self.glecf_account_rnc:
            try:
                self._glecf_refresh_account()
            except GlecfError:
                # Without an answer, the submission itself will say what is wrong.
                pass
        mismatch = self._glecf_rnc_mismatch_message()
        if mismatch:
            raise UserError(mismatch)

    def _glecf_set_environment(self, glecf_environment):
        dgii_environment = DGII_ENVIRONMENTS.get((glecf_environment or "").lower())
        if dgii_environment and self.l10n_do_ecf_service_env != dgii_environment:
            self.sudo().l10n_do_ecf_service_env = dgii_environment

    def action_glecf_check_connection(self):
        self.ensure_one()
        try:
            self._glecf_refresh_account()
            self._glecf_refresh_certificate()
        except GlecfError as error:
            raise UserError(error.message) from error
        mismatch = self._glecf_rnc_mismatch_message()
        if mismatch:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "danger",
                    "title": _("This API key is from another company"),
                    "message": mismatch,
                    "sticky": True,
                    "next": {"type": "ir.actions.client", "tag": "soft_reload"},
                },
            }
        if self.glecf_certificate_name:
            message = _("The API key works. Certificate of %(name)s, valid until %(date)s.") % {
                "name": self.glecf_certificate_name,
                "date": self.glecf_certificate_valid_until or "-",
            }
        else:
            message = _("The API key works. Now upload your digital certificate.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Connected to GLECF"),
                "message": message,
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }


def _certificate_holder(subject):
    """'CN=JUAN PEREZ,SERIALNUMBER=...' -> 'JUAN PEREZ'."""
    if not subject:
        return False
    for part in subject.split(","):
        key, _sep, value = part.partition("=")
        if key.strip().upper() == "CN" and value.strip():
            return value.strip()[:120]
    return subject[:120]
