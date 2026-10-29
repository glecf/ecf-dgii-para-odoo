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
    _description = "Subir el certificado digital a GLECF"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    certificate_file = fields.Binary(string="Certificado (.p12)", attachment=False)
    certificate_filename = fields.Char()
    certificate_password = fields.Char(string="Contraseña del certificado")

    def action_upload(self):
        self.ensure_one()
        company = self.company_id
        if not company.sudo().glecf_api_key:
            raise UserError(_("Pega primero tu clave de API de GLECF y después sube el certificado."))
        if not self.certificate_file:
            raise UserError(_("Elige el archivo del certificado (.p12 o .pfx)."))
        filename = self.certificate_filename or "certificate.p12"
        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            raise UserError(_("El certificado debe ser un archivo .p12 o .pfx."))

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
                    _("GLECF no pudo abrir el certificado: la contraseña no es correcta o el archivo está dañado.")
                ) from error
            raise UserError(error.message) from error

        company.sudo()._glecf_store_certificate(certificate)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Certificado subido a GLECF"),
                "message": _("Válido hasta %s. Tus e-CF se firmarán con él.")
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
