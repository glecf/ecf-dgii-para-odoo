import base64

from odoo import _, fields, models
from odoo.exceptions import UserError

from .glecf_client import GlecfError

ALLOWED_EXTENSIONS = (".p12", ".pfx")


class GlecfCertificateWizard(models.TransientModel):
    """Send the digital certificate to GLECF.

    The file and its password go straight to GLECF, which keeps them
    encrypted. Odoo does not keep either: both fields are emptied as soon as
    the certificate is sent, whether it worked or not.
    """

    _name = "glecf.certificate.wizard"
    _description = "Upload the digital certificate to GLECF"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    certificate_file = fields.Binary(string="Certificate (.p12)", attachment=False)
    certificate_filename = fields.Char()
    certificate_password = fields.Char(string="Certificate password")

    def action_upload(self):
        self.ensure_one()
        company = self.company_id
        if not company.sudo().glecf_api_key:
            raise UserError(_("Paste your GLECF API key first, then upload the certificate."))
        if not self.certificate_file:
            raise UserError(_("Choose the certificate file (.p12 or .pfx)."))
        filename = self.certificate_filename or "certificate.p12"
        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            raise UserError(_("The certificate must be a .p12 or .pfx file."))

        content = base64.b64decode(self.certificate_file)
        password = self.certificate_password or ""
        self._forget_certificate()

        client = company._glecf_client()
        try:
            certificate = client.upload_certificate(filename, content, password)
            if not certificate.get("is_default"):
                certificate = client.set_default_certificate(certificate["id"])
        except GlecfError as error:
            if "password" in error.message.lower() or "corrupt" in error.message.lower():
                raise UserError(
                    _("GLECF could not open the certificate: the password is wrong or the file is damaged.")
                ) from error
            raise UserError(error.message) from error

        company.sudo()._glecf_store_certificate(certificate)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Certificate uploaded to GLECF"),
                "message": _("Valid until %s. Your e-CF will be signed with it.")
                % (company.glecf_certificate_valid_until or "-"),
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def _forget_certificate(self):
        """Empty the file and the password in the database right now.

        Odoo saved this window before the button was pressed. A separate
        cursor commits the cleanup on its own, so the certificate is gone
        even if the upload fails and this transaction is rolled back.
        """
        with self.pool.cursor() as cr:
            cr.execute(
                "UPDATE glecf_certificate_wizard"
                " SET certificate_file = NULL, certificate_password = NULL"
                " WHERE id IN %s",
                [tuple(self.ids)],
            )
        self.invalidate_recordset(["certificate_file", "certificate_password"])
