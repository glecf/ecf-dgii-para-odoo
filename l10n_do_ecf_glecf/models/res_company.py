import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from .glecf_client import GlecfClient, GlecfError

REGISTER_URL = "https://glecf.com/register"
PLAN_URL = "https://app.glecf.com/cuenta"
CERTIFICATION_URL = "https://app.glecf.com/certificacion-dgii"
CONNECT_URL = "https://app.glecf.com/conectar/odoo"
CONNECT_PATH = "/glecf/conectar"
# A pending connection is kept this long: the person signs in to GLECF and
# presses Authorize, which takes a minute or two.
CONNECT_TIMEOUT = 30 * 60
CONNECT_PARAM = "glecf.connect."

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
        string="Clave de API de GLECF",
        groups="base.group_system",
        copy=False,
    )
    glecf_consent = fields.Boolean(
        string="Enviar mis facturas a GLECF",
        copy=False,
        help="Tus facturas e-CF se envían a GLECF, que las firma con tu certificado "
        "y las entrega a la DGII. No se envía nada hasta marcar esta casilla.",
    )
    glecf_auto_send = fields.Boolean(
        string="Enviar el e-CF al publicar",
        default=True,
        help="Envía cada e-CF un momento después de publicarlo. Si no está marcado, "
        "usa el botón «Enviar a la DGII» de la factura.",
    )
    l10n_do_ecf_service_env = fields.Selection(
        selection=[
            ("TesteCF", "Pruebas"),
            ("CerteCF", "Certificación"),
            ("eCF", "Producción"),
        ],
        string="Ambiente de la DGII",
        default="TesteCF",
        readonly=True,
        help="Lo fija tu clave de API de GLECF: cada clave es de un ambiente. "
        "Se actualiza con cada factura que procesa GLECF.",
    )

    # Only what helps to recognise the certificate. The certificate itself and
    # its password are kept by GLECF, never in Odoo.
    glecf_certificate_name = fields.Char(string="Titular del certificado", readonly=True, copy=False)
    glecf_certificate_valid_until = fields.Date(string="Certificado válido hasta", readonly=True, copy=False)

    # The GLECF company the API key belongs to, read when connecting.
    glecf_account_name = fields.Char(string="Cuenta de GLECF", readonly=True, copy=False)
    glecf_account_rnc = fields.Char(string="RNC de la cuenta de GLECF", readonly=True, copy=False)

    # The GLECF plan and what is left this month, read with the account.
    glecf_plan_label = fields.Char(string="Plan de GLECF", readonly=True, copy=False)
    glecf_ecf_limit = fields.Integer(readonly=True, copy=False)
    glecf_ecf_remaining = fields.Integer(readonly=True, copy=False)
    # Whether the DGII has the company as an electronic issuer, as GLECF read
    # it from the DGII directory (2026-10-29).
    glecf_issuer_state = fields.Selection(
        selection=[
            ("glecf", "Emisor electrónico con GLECF"),
            ("otro_programa", "Emisor electrónico con otro programa"),
            ("no", "Todavía no es emisor electrónico"),
            ("sin_respuesta", "No se pudo comprobar"),
        ],
        string="Emisor electrónico",
        readonly=True,
        copy=False,
    )
    glecf_account_checked = fields.Datetime(readonly=True, copy=False)
    glecf_plan_summary = fields.Char(compute="_compute_glecf_plan_summary")
    glecf_plan_low = fields.Boolean(compute="_compute_glecf_plan_summary")
    # The line under the plan name in Settings (the name goes in bold).
    glecf_plan_usage = fields.Char(compute="_compute_glecf_plan_summary")

    glecf_sequences_to_check = fields.Integer(compute="_compute_glecf_sequences_to_check")

    def _compute_glecf_plan_summary(self):
        for company in self:
            if not company.glecf_plan_label:
                company.glecf_plan_summary = False
                company.glecf_plan_low = False
                company.glecf_plan_usage = False
            elif company.glecf_ecf_limit < 0:
                company.glecf_plan_summary = _("Plan %s · comprobantes ilimitados") % company.glecf_plan_label
                company.glecf_plan_low = False
                company.glecf_plan_usage = _("Comprobantes ilimitados")
            else:
                company.glecf_plan_summary = _("Plan %(plan)s · te quedan %(left)s de %(limit)s comprobantes este mes") % {
                    "plan": company.glecf_plan_label,
                    "left": company.glecf_ecf_remaining,
                    "limit": company.glecf_ecf_limit,
                }
                # Only e-CF issued in Production count (GLECF, 2026-10-29).
                company.glecf_plan_usage = _(
                    "Te quedan %(left)s de %(limit)s comprobantes este mes. Solo cuentan los de Producción."
                ) % {"left": company.glecf_ecf_remaining, "limit": company.glecf_ecf_limit}
                # Warn from the last tenth of the month's e-CF.
                company.glecf_plan_low = company.glecf_ecf_remaining * 10 <= company.glecf_ecf_limit

    def _compute_glecf_sequences_to_check(self):
        types = self.env["l10n_do.account.journal.document_type"].sudo()
        for company in self:
            company.glecf_sequences_to_check = len(
                types.search(types._glecf_issued_domain(company)).filtered("glecf_expiry_unchecked")
            )

    def _glecf_check_admin(self):
        # These write the key, the account and the environment as superuser:
        # only an administrator may run them, also when called from outside
        # the Settings screen.
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Solo un administrador puede conectar la empresa con GLECF."))

    def action_glecf_sequences(self):
        """The e-NCF sequences of the e-CF types this company issues, in one list."""
        self.ensure_one()
        types = self.env["l10n_do.account.journal.document_type"]
        return {
            "type": "ir.actions.act_window",
            "name": _("Secuencias e-NCF"),
            "res_model": types._name,
            "view_mode": "list",
            "views": [(self.env.ref("l10n_do_ecf_glecf.glecf_sequences_view_tree").id, "list")],
            "domain": types._glecf_issued_domain(self),
            "target": "current",
        }

    def write(self, vals):
        if "glecf_api_key" in vals:
            # Another key may belong to another company: read it again.
            vals = dict(vals, glecf_account_name=False, glecf_account_rnc=False)
        res = super().write(vals)
        if vals.get("glecf_consent"):
            # What was waiting for the consent goes out now, not on the next pass.
            self.env.ref("l10n_do_ecf_glecf.ir_cron_glecf_sync").sudo()._trigger()
        return res

    def _glecf_odoo_rnc(self):
        return "".join(char for char in (self.vat or "") if char.isdigit())

    def _glecf_refresh_account(self):
        """Read which GLECF company and environment the API key belongs to."""
        account = self._glecf_client().current_account()
        self._glecf_store_account(account)
        return account

    def _glecf_store_account(self, account):
        values = {
            "glecf_account_name": account.get("name") or False,
            "glecf_account_rnc": account.get("rnc") or False,
        }
        values["glecf_account_checked"] = fields.Datetime.now()
        if account.get("emisor_electronico"):
            values["glecf_issuer_state"] = account["emisor_electronico"]
        if account.get("plan_label"):
            values.update({
                "glecf_plan_label": account["plan_label"],
                "glecf_ecf_limit": account.get("ecf_limit", -1),
                "glecf_ecf_remaining": account.get("ecf_remaining", -1),
            })
        self.sudo().write(values)
        self._glecf_set_environment(account.get("environment"))

    # -- Connect with one click ------------------------------------------------

    def action_glecf_connect(self):
        """Send the person to GLECF to authorize this Odoo; they come back connected.

        This Odoo keeps a secret (the verifier) and sends only its hash. GLECF
        gives back a one-time code, which this Odoo trades for an API key
        together with the verifier, server to server. So the API key never
        travels through the browser, and a code seen by someone else is useless.
        """
        self._glecf_check_admin()
        self.ensure_one()
        params = self.env["ir.config_parameter"].sudo()
        _forget_expired_connections(params)
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        redirect_uri = _base_url(self).rstrip("/") + CONNECT_PATH
        params.set_param(CONNECT_PARAM + state, json.dumps({
            "company_id": self.id,
            "uid": self.env.uid,
            "verifier": verifier,
            "redirect_uri": redirect_uri,
            "expires": time.time() + CONNECT_TIMEOUT,
        }))
        query = {
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": _challenge(verifier),
            "empresa": self.name or "",
            "rnc": self._glecf_odoo_rnc(),
        }
        return {
            "type": "ir.actions.act_url",
            "url": "%s?%s" % (CONNECT_URL, urlencode({k: v for k, v in query.items() if v})),
            "target": "self",
        }

    def _glecf_finish_connection(self, code, pending):
        """Trade the code for the API key and keep it. Raises GlecfError."""
        self.ensure_one()
        answer = GlecfClient(None).exchange(
            code, pending["verifier"], pending["redirect_uri"]
        )
        self.sudo().write({"glecf_api_key": answer["api_key"]})
        self._glecf_store_account(answer)
        try:
            # The exchange answer has no plan and no issuer state: without
            # this, Settings showed neither until «Check connection» or the
            # daily pass (seen in Enterprise, 2026-10-29).
            self._glecf_refresh_account()
        except GlecfError:
            pass
        try:
            self._glecf_refresh_certificate()
        except GlecfError:
            # Connected anyway; the certificate shows up on «Check connection».
            pass

    def _glecf_rnc_mismatch_message(self):
        """Why sending would fail, if the GLECF account is another company."""
        if self.glecf_account_rnc and self.glecf_account_rnc != self._glecf_odoo_rnc():
            return _(
                "La cuenta de GLECF de esta clave de API es %(account)s, RNC %(glecf)s, pero el RNC "
                "de %(company)s en Odoo es %(odoo)s. La DGII rechazaría todas las facturas: usa la "
                "clave de API de la cuenta de GLECF de esta empresa o corrige el RNC en Odoo."
            ) % {
                "account": self.glecf_account_name or "-",
                "glecf": self.glecf_account_rnc,
                "company": self.name,
                "odoo": self._glecf_odoo_rnc() or "-",
            }
        return False

    def action_glecf_create_account(self):
        """Open the GLECF sign-up with this company's data already filled in."""
        self._glecf_check_admin()
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
        return GlecfClient(self.sudo().glecf_api_key)

    def _glecf_check_ready(self):
        """Raise a clear error if this company cannot send e-CF yet."""
        self.ensure_one()
        if not self.sudo().glecf_api_key:
            raise UserError(
                _("Conecta primero %s con GLECF: Ajustes > Facturación > Facturación electrónica.") % self.name
            )
        if not self.glecf_consent:
            raise UserError(
                _("%s todavía no ha aceptado enviar sus facturas a GLECF (Ajustes > Facturación).") % self.name
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
        not_issuer = self._glecf_not_issuer_message()
        if not_issuer:
            raise UserError(not_issuer)

    def _glecf_not_issuer_message(self):
        """Why sending would fail: production, and the DGII does not have the
        company as an electronic issuer. «Could not check» blocks nothing."""
        if self.l10n_do_ecf_service_env == "eCF" and self.glecf_issuer_state == "no":
            return _(
                "%s todavía no es emisor electrónico ante la DGII, y en Producción la DGII "
                "rechazaría el e-CF. Completa la certificación en GLECF (%s)."
            ) % (self.name, CERTIFICATION_URL)
        return False

    def _glecf_set_environment(self, glecf_environment):
        dgii_environment = DGII_ENVIRONMENTS.get((glecf_environment or "").lower())
        if dgii_environment and self.l10n_do_ecf_service_env != dgii_environment:
            self.sudo().l10n_do_ecf_service_env = dgii_environment

    def action_glecf_check_connection(self):
        self._glecf_check_admin()
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
                    "title": _("Esta clave de API es de otra empresa"),
                    "message": mismatch,
                    "sticky": True,
                    "next": {"type": "ir.actions.client", "tag": "soft_reload"},
                },
            }
        if self.glecf_certificate_name:
            message = _("La clave de API funciona. Certificado de %(name)s, válido hasta %(date)s.") % {
                "name": self.glecf_certificate_name,
                "date": self.glecf_certificate_valid_until.strftime("%d/%m/%Y")
                if self.glecf_certificate_valid_until else "-",
            }
        else:
            message = _("La clave de API funciona. Ahora sube tu certificado digital.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Conectado con GLECF"),
                "message": message,
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }


def _base_url(company):
    """Where the person is browsing Odoo right now.

    `web.base.url` can still say http://localhost:8069 on a fresh install or
    behind a proxy; coming back there would fail. The address of the current
    request is the one that works for this person.
    """
    if request and request.httprequest:
        return request.httprequest.url_root
    return company.get_base_url()


def _challenge(verifier):
    """PKCE S256: base64url(sha256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _forget_expired_connections(params):
    """Drop connections nobody finished, so they do not pile up."""
    now = time.time()
    for record in params.search([("key", "=like", CONNECT_PARAM + "%")]):
        try:
            expired = json.loads(record.value).get("expires", 0) < now
        except (TypeError, ValueError):
            expired = True
        if expired:
            record.unlink()


def take_pending_connection(env, state):
    """The pending connection of `state`, removed so it is used only once."""
    if not state or len(state) > 64:
        return None
    params = env["ir.config_parameter"].sudo()
    record = params.search([("key", "=", CONNECT_PARAM + state)], limit=1)
    if not record:
        return None
    value = record.value
    record.unlink()
    try:
        pending = json.loads(value)
    except (TypeError, ValueError):
        return None
    if pending.get("expires", 0) < time.time():
        return None
    return pending


def _certificate_holder(subject):
    """'CN=JUAN PEREZ,SERIALNUMBER=...' -> 'JUAN PEREZ'."""
    if not subject:
        return False
    for part in subject.split(","):
        key, _sep, value = part.partition("=")
        if key.strip().upper() == "CN" and value.strip():
            return value.strip()[:120]
    return subject[:120]
