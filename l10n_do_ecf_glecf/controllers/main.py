from markupsafe import Markup, escape

from odoo import _, http
from odoo.http import request

from ..models.glecf_client import GlecfError
from ..models.res_company import CONNECT_PATH, take_pending_connection

SETTINGS_URL = "/web#action=account.action_account_config"


class GlecfConnect(http.Controller):

    @http.route(CONNECT_PATH, type="http", auth="user", methods=["GET"])
    def glecf_connect(self, state=None, code=None, error=None, **kwargs):
        """Where GLECF sends the person back after «Authorize» or «Cancel»."""
        env = request.env
        if not env.user.has_group("base.group_system"):
            return self._page(_("Solo un administrador puede conectar Odoo con GLECF."))

        pending = take_pending_connection(env, state)
        if not pending or pending.get("uid") != env.uid:
            return self._page(_("Este enlace de conexión caducó. Pulsa «Conectar con GLECF» otra vez."))
        if error or not code:
            return self._page(_("Se canceló la conexión. No se cambió nada."))

        company = env["res.company"].browse(pending["company_id"]).exists()
        if not company or company not in env.user.company_ids:
            return self._page(_("Este enlace de conexión caducó. Pulsa «Conectar con GLECF» otra vez."))
        try:
            company._glecf_finish_connection(code, pending)
        except GlecfError as failure:
            return self._page(failure.message)
        return request.redirect(SETTINGS_URL, local=True)

    def _page(self, message):
        html = Markup(
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>GLECF</title></head>"
            "<body style='font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;"
            "padding:0 1rem;color:#111827;line-height:1.6'>"
            "<p>%s</p><p><a href='%s' style='color:#2563eb'>%s</a></p></body></html>"
        ) % (escape(message), SETTINGS_URL, escape(_("Volver a Ajustes")))
        return request.make_response(html, headers=[("Content-Type", "text/html; charset=utf-8")])
