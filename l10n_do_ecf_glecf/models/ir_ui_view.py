import logging

from lxml import etree

from odoo import api, models
from odoo.tools import file_open

_logger = logging.getLogger(__name__)

OPCIONALES = "l10n_do_ecf_glecf/views/report_invoice_opcional.xml"


class IrUiView(models.Model):
    _inherit = "ir.ui.view"

    def _glecf_combined_arch(self):
        if hasattr(self, "_get_combined_arch"):
            return self._get_combined_arch()
        return etree.fromstring(self.get_combined_arch())

    @api.model
    def _glecf_apply_optional_touches(self):
        """Apply each touch of OPCIONALES only if its nodes exist.

        A missing node stops the whole install in Odoo, and a customer's invoice
        may not have them. Ajustes warns of the ones left inactive.
        """
        views = self.sudo()
        with file_open(OPCIONALES, "rb") as archivo:
            plantillas = etree.parse(archivo).getroot().iter("template")
        for plantilla in plantillas:
            nombre = plantilla.get("id")
            padre = views.env.ref(plantilla.get("inherit_id"))
            vista = views.env.ref("l10n_do_ecf_glecf." + nombre, raise_if_not_found=False)
            if vista:
                vista.active = False
                vista.flush_recordset(["active"])
            # During an install or update Odoo leaves out the views a customer
            # made by hand, so a node they removed looked present and the touch
            # broke every printed invoice (Zennio, 2026-11-25).
            try:
                arch = padre.with_context(load_all_views=True)._glecf_combined_arch()
            except Exception as error:
                # A customer view that no longer fits (it points at something
                # another view removed) breaks the whole template. The touch
                # stays off; it must never stop an update (Zennio, 2026-11-25).
                _logger.warning("GLECF: retoque %s sin aplicar: %s", nombre, error)
                continue
            xpaths = plantilla.findall("xpath")
            if not all(arch.xpath(xpath.get("expr")) for xpath in xpaths):
                continue
            cuerpo = etree.Element("data")
            cuerpo.extend(xpaths)
            texto = etree.tostring(cuerpo, encoding="unicode")
            if vista:
                vista.write({"arch": texto, "active": True})
                continue
            vista = views.create({
                "name": nombre,
                "type": "qweb",
                "mode": "extension",
                "inherit_id": padre.id,
                "arch": texto,
            })
            # noupdate: created here, not by a data file, so an update must keep it.
            views.env["ir.model.data"].sudo().create({
                "module": "l10n_do_ecf_glecf",
                "name": nombre,
                "model": "ir.ui.view",
                "res_id": vista.id,
                "noupdate": True,
            })
