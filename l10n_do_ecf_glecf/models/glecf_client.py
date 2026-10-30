"""Small client for the GLECF REST API.

A handful of calls: connect, check the API key, upload the certificate,
submit an e-CF and read it back. Everything else (signing, sending to the
DGII, retries) happens on the GLECF side.
"""

import logging

import requests

from odoo import _, release
from odoo.modules.module import get_manifest

_logger = logging.getLogger(__name__)

# The module only talks to GLECF: the address is fixed, with no setting to
# point it anywhere else.
API_URL = "https://api.glecf.com/api/v1"
TIMEOUT = 30


class GlecfError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.message = message
        self.status = status

    @property
    def transient(self):
        """No answer, a server error or too many requests: worth trying again.

        Any other error is GLECF saying no, and trying again changes nothing.
        """
        return self.status is None or self.status >= 500 or self.status == 429


class GlecfClient:
    def __init__(self, api_key):
        self.api_key = (api_key or "").strip()

    def _request(self, method, path, **kwargs):
        headers = {
            "Accept": "application/json",
            "User-Agent": "odoo-%s l10n_do_ecf_glecf" % release.version,
            # GLECF answers with the latest version, for the notice in Settings:
            # nobody's copy of the module updates by itself (2026-10-30).
            "X-Module-Version": get_manifest("l10n_do_ecf_glecf").get("version", ""),
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        try:
            response = requests.request(
                method,
                API_URL + path,
                headers=headers,
                timeout=TIMEOUT,
                **kwargs,
            )
        except requests.RequestException as error:
            _logger.warning("GLECF unreachable: %s", error)
            raise GlecfError(_("No se pudo contactar con GLECF. Inténtalo de nuevo en unos minutos.")) from error

        try:
            data = response.json()
        except ValueError:
            data = {}

        # 401 is always the key: its text comes from the server, in English.
        if response.status_code == 401 or (response.status_code == 403 and not _detail(data)):
            raise GlecfError(
                _("GLECF no aceptó la clave de API. Revísala en Ajustes."),
                response.status_code,
            )
        if response.status_code >= 400:
            raise GlecfError(
                _("GLECF respondió: %s") % (_detail(data) or response.reason),
                response.status_code,
            )
        return data

    def exchange(self, code, verifier, redirect_uri):
        """Trade the one-time code of «Connect to GLECF» for an API key.

        Needs no API key: the code and the verifier, which never left this
        Odoo, identify the caller.
        """
        return self._request(
            "POST",
            "/api-keys/exchange",
            json={"code": code, "code_verifier": verifier, "redirect_uri": redirect_uri},
        )

    def received_feed(self, after=None, limit=50):
        """The e-CF the company received, oldest first, from the cursor `after`."""
        params = {"limit": limit}
        if after:
            params["after"] = after
        return self._request("GET", "/documents/received/feed", params=params)

    def received_approval(self, received_id, estado, motivo=None):
        """Accept or reject a received e-CF (commercial approval)."""
        payload = {"estado": estado}
        if motivo:
            payload["motivo_rechazo"] = motivo
        return self._request("POST", "/documents/received/%s/approval" % received_id, json=payload)

    def check(self):
        """Raise GlecfError if the API key does not work."""
        return self._request("GET", "/documents", params={"limit": 1})

    def current_account(self):
        """RNC, name and environment of the GLECF company the key belongs to."""
        return self._request("GET", "/api-keys/current")

    def submit(self, payload):
        return self._request("POST", "/documents/submit", json=payload)

    def get_document(self, document_id):
        return self._request("GET", "/documents/%s" % document_id)

    def list_certificates(self):
        return self._request("GET", "/certificates").get("items", [])

    def upload_certificate(self, filename, content, password):
        return self._request(
            "POST",
            "/certificates",
            files={"file": (filename, content, "application/x-pkcs12")},
            data={"password": password or ""},
        )

    def set_default_certificate(self, certificate_id):
        return self._request("POST", "/certificates/%s/set-default" % certificate_id)


def _detail(data):
    """The human message of an error answer, whatever its shape."""
    if not isinstance(data, dict):
        return ""
    detail = data.get("message") or data.get("detail") or ""
    if isinstance(detail, list):
        detail = "; ".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail)
    elif isinstance(detail, dict):
        detail = detail.get("message") or str(detail)
    return str(detail)[:500]
