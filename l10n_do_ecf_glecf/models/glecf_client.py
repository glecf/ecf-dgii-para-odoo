"""Small client for the GLECF REST API.

Only three calls are needed: check the API key, submit an e-CF and read it
back. Everything else (signing, sending to the DGII, retries) happens on the
GLECF side.
"""

import logging

import requests

from odoo import _, release

_logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.glecf.com/api/v1"
TIMEOUT = 30


class GlecfError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.message = message
        self.status = status


class GlecfClient:
    def __init__(self, api_key, base_url=None):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_API_URL).rstrip("/")

    def _request(self, method, path, **kwargs):
        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "odoo-%s l10n_do_ecf_glecf" % release.version,
        }
        try:
            response = requests.request(
                method,
                self.base_url + path,
                headers=headers,
                timeout=TIMEOUT,
                **kwargs,
            )
        except requests.RequestException as error:
            _logger.warning("GLECF unreachable: %s", error)
            raise GlecfError(_("Could not reach GLECF. Try again in a few minutes.")) from error

        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code in (401, 403) and not _detail(data):
            raise GlecfError(
                _("GLECF did not accept the API key. Check it in Settings."),
                response.status_code,
            )
        if response.status_code >= 400:
            raise GlecfError(
                _("GLECF answered: %s") % (_detail(data) or response.reason),
                response.status_code,
            )
        return data

    def check(self):
        """Raise GlecfError if the API key does not work."""
        return self._request("GET", "/documents", params={"limit": 1})

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
